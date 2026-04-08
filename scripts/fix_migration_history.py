"""
One-time script to fake-mark missing migrations so the migration chain
is consistent, allowing `migrate` to run again.

Run with:
    python scripts/fix_migration_history.py
"""
import os
import sys
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
django.setup()

from django.db import connection

MISSING = [
    ("extraction_documents", "0001_initial"),
    ("extraction_documents", "0002_extractiondocument_declared_country_code_and_more"),
]

with connection.cursor() as cur:
    for app, name in MISSING:
        cur.execute(
            "SELECT COUNT(*) FROM django_migrations WHERE app=%s AND name=%s",
            [app, name],
        )
        (count,) = cur.fetchone()
        if count == 0:
            cur.execute(
                "INSERT INTO django_migrations (app, name, applied) VALUES (%s, %s, NOW())",
                [app, name],
            )
            print(f"  [INSERTED]  {app}.{name}")
        else:
            print(f"  [ALREADY]   {app}.{name}")

print("Done.")
