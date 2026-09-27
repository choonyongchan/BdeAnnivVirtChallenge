# One-off: register the daily 23:49 backfill task (re-run to update it). Same principal
# and settings as BdeAnnivVirtChallenge-HourlyPipeline: runs as the current user whether
# or not they're logged on (S4U, no stored password), skips a run if one is in flight.
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $RepoRoot "scripts\run_backfill.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At "23:49"
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName "BdeAnnivVirtChallenge-DailyBackfill" -Force `
    -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings `
    -Description "Daily 23:49 backfill of activities the club feed missed, from each member's Strava profile; then regenerates and publishes the dashboard."
