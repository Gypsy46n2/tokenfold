# TokenFold — Windows installer
# Creates an isolated venv next to this script and installs the core package.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$core = Join-Path (Split-Path -Parent $here) "core"
if (-not (Test-Path $core)) { throw "core/ not found next to windows/ - keep the folder structure intact" }

Write-Host "Creating venv..." -ForegroundColor Cyan
python -m venv (Join-Path $here ".venv")
$pip = Join-Path $here ".venv\Scripts\pip.exe"

Write-Host "Installing TokenFold core..." -ForegroundColor Cyan
& $pip install --upgrade pip -q
& $pip install -e $core -q

Write-Host ""
Write-Host "Done. Start the proxy with:  .\start.ps1" -ForegroundColor Green
Write-Host "Dashboard (once running):    http://localhost:9339/tokenfold/dashboard"
