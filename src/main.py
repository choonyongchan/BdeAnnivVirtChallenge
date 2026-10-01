"""Scrape, then generate and publish the dashboard; the first failure stops it.
Hourly: the club feed only (members + new foot activities), a handful of requests.
Nightly (the 23:xx run, or --full): also the leaderboard and every member's profile week -> weekly.csv,
the authoritative snapshot the hourly feed activities are added on top of.
    python -m src.main [--full]
"""
import json
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .activities.activities import fetch_leaderboard, sync_weeks
from .config import settings
from .dashboard import generate
from .members.members import scrape_members
from .strava_session import AUTH_PATH, ScrapeError

REPO_ROOT = generate.REPO_ROOT

REAUTH_MSG = (
    "\n==================== STRAVA RE-AUTH REQUIRED ====================\n"
    "The saved Strava session is missing or expired.\n\n"
    "  1. python -m src.login          # opens a browser, log in\n"
    "  2. Re-encode the session:\n"
    "     PowerShell: [Convert]::ToBase64String([IO.File]::ReadAllBytes('src/auth_state.json'))\n"
    "     bash:       base64 -w0 src/auth_state.json\n"
    "  3. Paste the result into the GitHub Actions secret  AUTH_STATE\n"
    "===============================================================\n"
)


def check_auth() -> None:
    """Exit with REAUTH_MSG before launching a browser if the saved session is absent or malformed.
    An expired but well-formed session gets past this and is caught by the scrape itself."""
    try:
        state = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit(REAUTH_MSG)
    if not state.get("cookies"):
        raise SystemExit(REAUTH_MSG)


def publish_dashboard() -> None:
    """Commit and push index.html and its user-count.json badge so GitHub Actions redeploys; no-op if unchanged."""
    subprocess.run(["git", "add", str(generate.OUT_PATH), str(generate.USER_COUNT_PATH)],
                   cwd=REPO_ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT).returncode == 0:
        print("index.html unchanged, nothing to publish.")
        return
    message = f"🏃 Dashboard update {datetime.now():%Y-%m-%d %H:%M}"
    subprocess.run(["git", "commit", "-m", message], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)
    print("Pushed index.html.")


def main() -> None:
    check_auth()
    full = "--full" in sys.argv or datetime.now(ZoneInfo(settings.timezone)).hour == 23
    try:
        print(f"=== scrape {'leaderboard + ' if full else ''}members + feed ===", flush=True)
        leaderboard = fetch_leaderboard() if full else {}
        scrape_members(leaderboard)

        if full:
            print("\n=== nightly snapshot: every member's profile week ===", flush=True)
            sync_weeks(leaderboard)
    except ScrapeError as e:
        raise SystemExit(f"scrape failed, pipeline stopped: {e}\n{REAUTH_MSG}")

    print("\n=== generate dashboard ===", flush=True)
    generate.run()

    print("\n=== publish dashboard ===", flush=True)
    publish_dashboard()

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
