"""Test settings -- overrides config.settings with SQLite in-memory DB.

Import everything from the main settings then override just DATABASES.
"""
import os

# Temporarily stub out MySQLdb before config.settings imports it
import sys
from unittest.mock import MagicMock
sys.modules['MySQLdb'] = MagicMock()
sys.modules['MySQLdb.constants'] = MagicMock()
sys.modules['MySQLdb.constants.CLIENT'] = MagicMock()
sys.modules['MySQLdb.constants.FIELD_TYPE'] = MagicMock()

from config.settings import *  # noqa: F401, F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# ---------------------------------------------------------------------------
# Skip migrations: create tables directly from model state.
# Running 144+ migration files against SQLite in-memory on every test run
# adds 60-120s of overhead.  Setting each app's migration module to None
# tells Django's test runner to use CREATE TABLE from the model definitions
# instead, cutting test startup to a few seconds.
# ---------------------------------------------------------------------------
MIGRATION_MODULES = {app.split(".")[-1]: None for app in INSTALLED_APPS if app.startswith("apps.")}
# Also disable migrations for django_celery_results (third-party)
MIGRATION_MODULES["django_celery_results"] = None

# Disable external services for tests
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Disable Langfuse -- the client reads env vars directly via os.getenv(),
# so we must unset them to prevent API calls during tests.
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.pop("LANGFUSE_SECRET_KEY", None)
os.environ.pop("LANGFUSE_HOST", None)

print("\n*** TEST SETTINGS: SQLite in-memory DB ***\n")
