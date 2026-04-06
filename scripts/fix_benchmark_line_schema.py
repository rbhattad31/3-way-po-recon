"""One-off script: fix source_type column default and clear orphaned procurement data."""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.db import connection

with connection.cursor() as c:
    sql = (
        "ALTER TABLE procurement_benchmark_result_line "
        "MODIFY source_type varchar(30) NOT NULL DEFAULT ''"
    )
    c.execute(sql)
    print("[OK] source_type column: default set to empty string")

    c.execute("DELETE FROM procurement_supplier_quotation")
    print("[OK] Orphaned supplier quotations cleared")

    c.execute("DELETE FROM procurement_request")
    print("[OK] All procurement requests cleared")
