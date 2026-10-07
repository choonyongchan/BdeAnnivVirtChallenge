# Hourly Windows Task Scheduler entry point: runs the pipeline and commits the ledgers
# (backend/auth_state.json and backend/nominal_roll/nominal_roll.csv must already exist locally).
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "run_$(Get-Date -Format 'yyyy-MM-dd_HHmmss').log"

Start-Transcript -Path $LogFile

try {
    $AuthState = Join-Path $RepoRoot "backend\auth_state.json"
    if (-not (Test-Path $AuthState)) {
        throw "backend/auth_state.json is missing. Run 'python -m backend.login' to re-authenticate."
    }

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
