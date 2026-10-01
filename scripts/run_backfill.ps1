# Daily Windows Task Scheduler entry point (23:49 SGT): backfill today's activities the
# club feed missed from every member's Strava profile, then regenerate and publish the
# dashboard. Registered by scripts/register_backfill_task.ps1.
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "backfill_$(Get-Date -Format 'yyyy-MM-dd_HHmmss').log"

Start-Transcript -Path $LogFile

try {
    $AuthState = Join-Path $RepoRoot "src\auth_state.json"
    if (-not (Test-Path $AuthState)) {
        throw "src/auth_state.json is missing. Run 'python -m src.login' to re-authenticate."
    }

    # The hourly pipeline runs at :47 and also appends to activities.csv and pushes;
    # let its 23:47 run finish first (it normally takes ~2 minutes).
    $Deadline = (Get-Date).AddMinutes(20)
    while ((Get-ScheduledTask -TaskName "BdeAnnivVirtChallenge-HourlyPipeline" -ErrorAction SilentlyContinue).State -eq "Running") {
        if ((Get-Date) -gt $Deadline) { throw "Hourly pipeline still running after 20 minutes." }
        Write-Host "Waiting for the hourly pipeline to finish..."
        Start-Sleep -Seconds 15
    }

    $Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    # Find roll members with no club-feed activity yet, so the backfill below scans them too.
    & $Python -m src.nominal_roll.nominal_roll --recheck
    if ($LASTEXITCODE -ne 0) {
        throw "nominal_roll --recheck exited with code $LASTEXITCODE (if the session expired: python -m src.login)"
    }

    & $Python -m src.activities.activities
    if ($LASTEXITCODE -ne 0) {
        throw "src.activities.activities exited with code $LASTEXITCODE"
    }

    git diff --quiet -- src/activities/activities.csv src/members/members.csv
    if ($LASTEXITCODE -eq 0) {
        Write-Host "No missing activities; nothing to publish."
    } else {
        & $Python -m src.dashboard.generate
        if ($LASTEXITCODE -ne 0) {
            throw "src.dashboard.generate exited with code $LASTEXITCODE"
        }
        git add src/activities/activities.csv src/members/members.csv index.html src/user-count.json
        git pull --rebase --autostash origin main
        git commit -m "Backfill update (Windows Task Scheduler)"
        git push
    }

    Write-Host "Backfill complete."
} finally {
    Stop-Transcript
}
