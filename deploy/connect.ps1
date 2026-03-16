# Connect to Finance Agents Server via SSH
# Usage: .\deploy\connect.ps1

$PEM_FILE = "C:\Users\RohitBhattad\Downloads\finance-agents.pem"
$SERVER_HOST = "20.244.26.58"
$SERVER_USER = "azureuser"
$SERVER_PORT = 22

# Verify PEM file exists
if (-not (Test-Path $PEM_FILE)) {
    Write-Error "PEM file not found: $PEM_FILE"
    exit 1
}

Write-Host "Connecting to $SERVER_USER@$SERVER_HOST..."
ssh -i $PEM_FILE -p $SERVER_PORT "$SERVER_USER@$SERVER_HOST"
