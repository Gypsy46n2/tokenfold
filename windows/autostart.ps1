# TokenFold — register a Scheduled Task so the proxy starts at logon
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here ".venv\Scripts\pythonw.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "-m tokenfold.cli serve --port 9339"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "TokenFold" -Action $action -Trigger $trigger -Force | Out-Null
Write-Host "Registered. TokenFold will start automatically at logon."
Write-Host "Remove with: Unregister-ScheduledTask -TaskName TokenFold -Confirm:`$false"
