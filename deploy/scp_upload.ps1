# Upload files to Finance Agents Server via SCP
# Usage: .\deploy\scp_upload.ps1 <local_path> <remote_path>
# Example: .\deploy\scp_upload.ps1 .\requirements.txt /home/ubuntu/app/

param(
    [Parameter(Mandatory=$true)][string]$LocalPath,
    [Parameter(Mandatory=$true)][string]$RemotePath
)

$PEM_FILE = "C:\Users\RohitBhattad\Downloads\finance-agents.pem"
$SERVER_HOST = "20.244.26.58"
$SERVER_USER = "azureuser"
$SERVER_PORT = 22

if (-not (Test-Path $PEM_FILE)) {
    Write-Error "PEM file not found: $PEM_FILE"
    exit 1
}

if (-not (Test-Path $LocalPath)) {
    Write-Error "Local file not found: $LocalPath"
    exit 1
}

Write-Host "Uploading $LocalPath to ${SERVER_USER}@${SERVER_HOST}:${RemotePath}..."
scp -i $PEM_FILE -P $SERVER_PORT $LocalPath "${SERVER_USER}@${SERVER_HOST}:${RemotePath}"
