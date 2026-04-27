#!/usr/bin/env python
"""Deploy azure_test_setup.sql to Azure SQL Server"""

import pyodbc
import sys
import os

server = 'streamline.database.windows.net'
database = 'streamline-db'
username = 'streamline'
password = 'bradsol@123'

print(f"Attempting to connect to {server}/{database}...")
print(f"Connection parameters:")
print(f"  Server: {server}")
print(f"  Database: {database}")
print(f"  User: {username}")
print(f"  Port: 1433 (ENCRYPTED)")
print()

try:
    # Build connection string with Azure-specific parameters
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
    
    print("Connecting (timeout 60 seconds)...")
    conn = pyodbc.connect(conn_str, timeout=60)
    print("✓ Connection successful!")
    
    cursor = conn.cursor()
    
    # Test query
    cursor.execute("SELECT @@VERSION")
    version = cursor.fetchone()
    print(f"✓ SQL Server version: {version[0][:60]}...")
    
    # Read SQL file
    print()
    print("Reading SQL file...")
    sql_file = 'imports_formats/azure_test_setup.sql'
    
    if not os.path.exists(sql_file):
        print(f"✗ Error: {sql_file} not found")
        sys.exit(1)
    
    with open(sql_file, 'r', encoding='utf-8') as f:
        sql_content = f.read()
    
    print(f"✓ SQL file loaded: {len(sql_content)} bytes")
    print()
    
    # Split by GO and execute with proper line handling
    import re
    
    # Split by GO on line boundaries (like sqlcmd)
    lines = sql_content.split('\n')
    batches = []
    current_batch = []
    
    for line in lines:
        # Check if line is just GO (case-insensitive)
        if re.match(r'^\s*GO\s*$', line, re.IGNORECASE):
            if current_batch:
                batch_text = '\n'.join(current_batch).strip()
                if batch_text:
                    batches.append(batch_text)
                current_batch = []
        else:
            current_batch.append(line)
    
    # Don't forget the last batch if there's no trailing GO
    if current_batch:
        batch_text = '\n'.join(current_batch).strip()
        if batch_text:
            batches.append(batch_text)
    
    print(f"Executing {len(batches)} batches...")
    
    success_count = 0
    error_count = 0
    
    for i, batch in enumerate(batches, 1):
        try:
            # Skip empty batches and comments-only batches
            clean_batch = '\n'.join(
                line for line in batch.split('\n') 
                if line.strip() and not line.strip().startswith('--')
            ).strip()
            
            if not clean_batch:
                continue
                
            cursor.execute(clean_batch)
            success_count += 1
            if i % 5 == 0:
                print(f"  {i}/{len(batches)} batches executed...")
        except Exception as e:
            error_count += 1
            err_msg = str(e)[:150]
            print(f"  Batch {i}: Warning - {err_msg}")
    
    # Commit all changes
    print()
    print("Committing changes...")
    conn.commit()
    print("✓ All changes committed!")
    
    # Print summary
    print()
    print("=" * 60)
    print(f"SUCCESS: SQL deployment completed!")
    print(f"  Statements executed: {success_count}")
    print(f"  Warnings/non-fatal errors: {error_count}")
    print("=" * 60)
    
    conn.close()
    
except pyodbc.DatabaseError as e:
    print(f"✗ Database Error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"✗ Error: {e}")
    sys.exit(1)
