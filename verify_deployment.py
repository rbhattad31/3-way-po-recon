#!/usr/bin/env python
"""Verify deployed ERP data"""

import pyodbc

server = 'streamline.database.windows.net'
database = 'streamline-db'
username = 'streamline'
password = 'bradsol@123'

try:
    conn_str = (
        f'Driver={{ODBC Driver 17 for SQL Server}};'
        f'Server=tcp:{server},1433;'
        f'Database={database};'
        f'Uid={username};'
        f'Pwd={password};'
        f'Encrypt=yes;'
        f'TrustServerCertificate=no;'
        f'Connection Timeout=60;'
    )
    
    conn = pyodbc.connect(conn_str, timeout=60)
    cursor = conn.cursor()
    
    print("=" * 70)
    print("ERP DEPLOYMENT VERIFICATION")
    print("=" * 70)
    print()
    
    # Check table creation
    tables = [
        'Master_Table',
        'Transaction_Header_Table', 
        'Transaction_ItemBody_Table',
        'Transaction_Payments_Table',
        'EFIMRDetailsTable'
    ]
    
    print("TABLE STATUS:")
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = '{table}'")
        exists = cursor.fetchone()[0]
        status = "✓ EXISTS" if exists else "✗ MISSING"
        print(f"  {table:40} {status}")
    
    print()
    print("DATA COUNTS:")
    
    # Count POs
    cursor.execute("SELECT COUNT(*) FROM Transaction_Header_Table WHERE VoucherSeries LIKE 'App PO%'")
    po_count = cursor.fetchone()[0]
    print(f"  Purchase Orders:              {po_count:5} rows")
    
    # Count invoices
    cursor.execute("SELECT COUNT(*) FROM Transaction_Payments_Table WHERE VoucherSeries = 'App PI'")
    inv_count = cursor.fetchone()[0]
    print(f"  Invoices (Payments):          {inv_count:5} rows")
    
    # Count GRNs
    cursor.execute("SELECT COUNT(*) FROM EFIMRDetailsTable")
    grn_count = cursor.fetchone()[0]
    print(f"  GRN Details:                  {grn_count:5} rows")
    
    # Count vendors
    cursor.execute("SELECT COUNT(*) FROM Master_Table WHERE MasterType = 'Vendor'")
    vendor_count = cursor.fetchone()[0]
    print(f"  Vendors:                      {vendor_count:5} rows")
    
    # Count items
    cursor.execute("SELECT COUNT(*) FROM Master_Table WHERE MasterType = 'Item'")
    item_count = cursor.fetchone()[0]
    print(f"  Items:                        {item_count:5} rows")
    
    print()
    print("LIVE DATA VERIFICATION:")
    
    # Check live POs
    for po_id in [616, 680, 13, 288]:
        cursor.execute(
            f"SELECT COUNT(*) FROM Transaction_Header_Table WHERE VoucherSeries = 'App PO' AND VoucherNo = {po_id}"
        )
        count = cursor.fetchone()[0]
        status = "✓" if count > 0 else "✗"
        print(f"  {status} PO {po_id}")
    
    print()
    print("SCENARIO DATA VERIFICATION:")
    
    # Check scenario POs
    scenario_pos = [1004, 1005, 1006, 1007, 1008, 1010]
    for po_id in scenario_pos:
        cursor.execute(
            f"SELECT PartyRefDoc FROM Transaction_Header_Table WHERE VoucherSeries = 'App PO' AND VoucherNo = {po_id}"
        )
        result = cursor.fetchone()
        po_num = result[0] if result else "MISSING"
        status = "✓" if result else "✗"
        print(f"  {status} Scenario PO {po_id}: {po_num}")
    
    print()
    print("=" * 70)
    print("✓ DEPLOYMENT VERIFICATION COMPLETE")
    print("=" * 70)
    
    conn.close()
    
except Exception as e:
    print(f"✗ Error: {e}")
    import sys
    sys.exit(1)
