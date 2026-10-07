"""Scrape what config.yaml's schedule says is due this hour, then generate and publish the dashboard; the first
failure stops it.
RecentActivities (default hourly): the club feed (members + new foot activities), a handful of requests.
New members it adds: every profile week since challenge_start, straight away.
MemberActivities + MemberStatistics (default 23:xx, or --full): the leaderboard, roll usernames found by athlete search
(NominalRollMembers) and every member's profile week ->
missed activities + today's cumulative rows in daily.csv, the authoritative snapshot the feed is added on top of.
    python -m backend.main [--full]
"""
import json
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .activities import member_activities, recent_activities
from .config import settings
from frontend import generate
from .members import members
from .strava_session import AUTH_PATH, ScrapeError

REPO_ROOT = generate.REPO_ROOT

REAUTH_MSG = (
    "\n==================== STRAVA RE-AUTH REQUIRED ====================\n"
    "The saved Strava session is missing or expired.\n\n"
    "  1. python -m backend.login          # opens a browser, log in\n"
    "  2. Re-encode the session:\n"
    "     PowerShell: [Convert]::ToBase64String([IO.File]::ReadAllBytes('backend/auth_state.json'))\n"
    "     bash:       base64 -w0 backend/auth_state.json\n"
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


def due(hours, hour: int) -> bool:
    """Whether a config.yaml schedule ("*" or a list of hours) includes this hour."""
    return hours == "*" or hour in hours   # ponytail: whole hours only, Task Scheduler wakes hourly at :47


def main() -> None:
    check_auth()
    hour = datetime.now(ZoneInfo(settings.timezone)).hour
    full = "--full" in sys.argv or due(settings.member_scan_hours, hour)
    try:
        leaderboard = member_activities.fetch_leaderboard() if full else {}
        new = []
        if full or due(settings.recent_activities_hours, hour):   # the scan needs leaderboard athletes in members.csv
            print("=== RecentActivities: club feed + members ===", flush=True)
            new = recent_activities.run(leaderboard)

        if full:
            print("\n=== NominalRollMembers: roll usernames via athlete search ===", flush=True)
            new += members.add_nominal_roll()

        if new:   # ponytail: a failed newcomer scan isn't retried; --setup repairs it
            print(f"\n=== MemberActivities + MemberStatistics: {len(new)} new members, every week ===", flush=True)
            member_activities.run(leaderboard, only=new)

        if full:
            print("\n=== MemberActivities + MemberStatistics: every member's last 2 profile weeks ===", flush=True)
            member_activities.run(leaderboard)
    except ScrapeError as e:
        raise SystemExit(f"scrape failed, pipeline stopped: {e}\n{REAUTH_MSG}")

    print("\n=== generate dashboard ===", flush=True)
    generate.run()

    print("\n=== publish dashboard ===", flush=True)
    publish_dashboard()

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
