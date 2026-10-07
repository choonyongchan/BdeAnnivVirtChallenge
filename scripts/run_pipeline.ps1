# Hourly Windows Task Scheduler entry point: runs the pipeline, then commits and pushes the ledgers and the site
# (backend/auth_state.json and backend/nominal_roll/nominal_roll.csv must already exist locally;
# backend.main exits with re-auth instructions when the session is missing).
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "run_$(Get-Date -Format 'yyyy-MM-dd_HHmmss').log"

Start-Transcript -Path $LogFile

try {
    $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    & $Python -m backend.main
    if ($LASTEXITCODE -ne 0) {
        throw "backend.main exited with code $LASTEXITCODE"
    }

    git add backend/statistics/statistics.csv backend/activities/activities.csv backend/members/members.csv backend/members/member_count.csv frontend/public
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "No ledger changes."
    } else {
        git pull --rebase --autostash origin main
        git commit -m "Ledger update (Windows Task Scheduler)"
        git push
    }

    Write-Host "Pipeline complete."
} finally {
    Stop-Transcript
}
