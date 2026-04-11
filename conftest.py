"""Root conftest -- overrides DATABASES to use SQLite for tests when MySQL is unavailable.

IMPORTANT: The DB override must happen BEFORE Django loads settings.py (which imports
the MySQL backend). We monkey-patch the settings module directly inside pytest_configure
so that when Django later reads DATABASES it gets SQLite instead of MySQL.
"""
import os
import sys


def pytest_configure(config):
    # Disable Langfuse during tests -- client reads env vars directly.
    os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    os.environ.pop("LANGFUSE_SECRET_KEY", None)
    os.environ.pop("LANGFUSE_HOST", None)
    """Switch to SQLite in-memory for test runs.

    This hook fires before Django is set up, so we can safely override DATABASES
    without triggering a MySQLdb import.
    """
    # Inject a fake django.db.backends.mysql so the import in settings.py does not crash.
    # The real override happens below by replacing DATABASES after Django configures.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    # Patch the config.settings module directly before Django loads it.
    # We do this by hooking into the import system via a custom finder.
    _patch_settings_for_sqlite()


def _patch_settings_for_sqlite():
    """Inject a SQLite DATABASES config into config.settings before Django reads it."""
    import importlib
    import importlib.util
    import types

    settings_module_name = "config.settings"

    # If already imported, just patch it directly
    if settings_module_name in sys.modules:
        mod = sys.modules[settings_module_name]
        mod.DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        }
        # Skip migrations -- create tables directly from model state.
        _apps = getattr(mod, "INSTALLED_APPS", [])
        mod.MIGRATION_MODULES = {a.split(".")[-1]: None for a in _apps if a.startswith("apps.")}
        mod.MIGRATION_MODULES["django_celery_results"] = None
        print("\n*** TEST DB OVERRIDE (already loaded): SQLite in-memory ***\n")
        return

    # Install a meta path finder that intercepts config.settings and patches DATABASES
    class _SQLiteSettingsPatcher:
        """Meta path finder that patches DATABASES in config.settings on import."""

        def find_module(self, fullname, path=None):
            if fullname == settings_module_name:
                return self
            return None

        def load_module(self, fullname):
            if fullname in sys.modules:
                return sys.modules[fullname]
            spec = importlib.util.find_spec(fullname)
            if spec is None:
                raise ImportError(f"Cannot find {fullname}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[fullname] = mod
            spec.loader.exec_module(mod)
            # Patch DATABASES to SQLite
            mod.DATABASES = {
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            }
            # Skip migrations -- create tables directly from model state.
            _apps = getattr(mod, "INSTALLED_APPS", [])
            mod.MIGRATION_MODULES = {a.split(".")[-1]: None for a in _apps if a.startswith("apps.")}
            mod.MIGRATION_MODULES["django_celery_results"] = None
            print("\n*** TEST DB OVERRIDE: SQLite in-memory ***\n")
            return mod

    sys.meta_path.insert(0, _SQLiteSettingsPatcher())
