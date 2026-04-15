"""Root conftest for test environment setup.

Use the dedicated ``config.test_settings`` module so pytest runs against SQLite
without relying on custom import-hook patching.
"""
import os


def pytest_configure(config):
    # Disable Langfuse during tests -- client reads env vars directly.
    os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    os.environ.pop("LANGFUSE_SECRET_KEY", None)
    os.environ.pop("LANGFUSE_HOST", None)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.test_settings")
