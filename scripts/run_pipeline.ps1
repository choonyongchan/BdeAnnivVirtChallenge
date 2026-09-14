# Hourly Windows Task Scheduler entry point. Mirrors .github/workflows/update.yml's
# "Run pipeline" + "Commit ledgers" steps, minus the CI-only secret-restore steps
# (src/auth_state.json and src/nominal_roll/nominal_roll.csv already exist locally).
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "run_$(Get-Date -Format 'yyyy-MM-dd_HHmmss').log"

Start-Transcript -Path $LogFile

try {
    $AuthState = Join-Path $RepoRoot "src\auth_state.json"
    if (-not (Test-Path $AuthState)) {
        throw "src/auth_state.json is missing. Run 'python -m src.login' to re-authenticate."
    }

    $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    & $Python -m src.main
    if ($LASTEXITCODE -ne 0) {
        throw "src.main exited with code $LASTEXITCODE"
    }

    git add src/activities/activities.csv src/members/members.csv index.html src/user-count.json
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
