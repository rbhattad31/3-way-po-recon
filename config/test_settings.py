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

# Disable external services for tests
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Disable Langfuse -- the client reads env vars directly via os.getenv(),
# so we must unset them to prevent API calls during tests.
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.pop("LANGFUSE_SECRET_KEY", None)
os.environ.pop("LANGFUSE_HOST", None)

print("\n*** TEST SETTINGS: SQLite in-memory DB ***\n")
