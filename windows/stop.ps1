# TokenFold — stop the proxy
param([int]$Port = 9339)
$conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($conns) {
    $conns | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -Confirm:$false }
    Write-Host "TokenFold stopped."
} else {
    Write-Host "Nothing listening on port $Port."
}
