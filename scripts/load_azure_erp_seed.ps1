$ErrorActionPreference = 'Stop'

$python = 'c:/3-way-po-recon/.venv/Scripts/python.exe'
$scriptPath = 'c:\3-way-po-recon\imports_formats\azure_test_setup.sql'

$pw = & $python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); import django; django.setup(); from apps.erp_integration.models import ERPConnection; from apps.erp_integration.crypto import decrypt_value; c=ERPConnection.objects.get(id=2); print(decrypt_value(c.db_password_encrypted), end='')"
if (-not $pw) {
    throw 'Failed to resolve Azure SQL password from ERP connection profile.'
}

$env:SQLCMDPASSWORD = $pw
try {
    sqlcmd -S "tcp:streamline.database.windows.net,1433" -d "streamline-db" -U "streamline" -N -C -i $scriptPath
    if ($LASTEXITCODE -ne 0) {
        throw "sqlcmd import failed with exit code $LASTEXITCODE"
    }

    sqlcmd -S "tcp:streamline.database.windows.net,1433" -d "streamline-db" -U "streamline" -N -C -Q "SET NOCOUNT ON; SELECT COUNT(*) AS scenario_pos FROM Transaction_Header_Table WHERE VoucherSeries='App PO' AND VoucherNo IN (13,680,1004,1005,1006,1007,1008,1010); SELECT COUNT(*) AS grn_rows_for_1004 FROM EFIMRDetailsTable WHERE POrderNum = 1004; SELECT COUNT(*) AS dup_rows FROM Transaction_Payments_Table WHERE SupplierInvNo='INV-ACME-2026-0045' AND PartyAccount='ACME Supplies Pvt Ltd';"
    if ($LASTEXITCODE -ne 0) {
        throw "sqlcmd verification failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item Env:SQLCMDPASSWORD -ErrorAction SilentlyContinue
}
