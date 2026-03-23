"""Root conftest — overrides DATABASES to use SQLite for tests when MySQL is unavailable."""
import django
from django.conf import settings


def pytest_configure(config):
    """Switch to SQLite in-memory for test runs when MySQL is not available."""
    settings.DATABASES["default"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
    print(f"\n*** TEST DB OVERRIDE: {settings.DATABASES['default']['ENGINE']} ***\n")
