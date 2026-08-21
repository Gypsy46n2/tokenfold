# TokenFold — start the proxy detached (survives closing the terminal)
param([int]$Port = 9339)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run install.ps1 first" }
$log = Join-Path $here "tokenfold.log"
Start-Process -FilePath $py `
    -ArgumentList "-m","tokenfold.cli","serve","--port","$Port" `
    -WindowStyle Hidden -RedirectStandardOutput $log `
    -RedirectStandardError (Join-Path $here "tokenfold.err.log")
Start-Sleep -Seconds 2
Write-Host "TokenFold running in background on http://localhost:$Port/v1 (log: tokenfold.log)"
Write-Host "Stop it with: .\stop.ps1"
