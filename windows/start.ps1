# TokenFold — start the proxy (Windows)
# Usage: .\start.ps1 [-Port 9339] [-Upstream http://localhost:11434/v1]
param(
    [int]$Port = 9339,
    [string]$Upstream = ""
)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run install.ps1 first" }
$args = @("-m", "tokenfold.cli", "serve", "--port", "$Port")
if ($Upstream) { $args += @("--upstream", $Upstream) }
Write-Host "TokenFold proxy -> http://localhost:$Port/v1   (Ctrl+C to stop)" -ForegroundColor Green
& $py @args
