"""Scrape what config.yaml's schedule says is due this hour, then generate and publish the dashboard; the first
failure stops it.
Hourly:  Members (members page -> members.csv, member_count.csv), Feed (club feed -> activities.csv),
         Statistics (leaderboard + ledger fallback -> statistics.csv).
Nightly (member_scan hours, or --full): MemberActivities too - every current member's last two profile weeks, every
         week since challenge_start for members ingested since the last scan (--setup: everyone) -> activities.csv
         gaps and profile figures in statistics.csv.
    python -m backend.main [--full] [--setup]
"""
import json
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from frontend import generate

from .activities import feed, member_activities
from .config import settings
from .members import members
from .statistics import statistics
from .strava_session import AUTH_PATH, ScrapeError

REPO_ROOT = generate.REPO_ROOT

REAUTH_MSG = (
    "\n==================== STRAVA RE-AUTH REQUIRED ====================\n"
    "The saved Strava session is missing or expired.\n\n"
    "  python -m backend.login          # opens a browser, log in\n"
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
    """Commit and push the static site so GitHub Actions redeploys; no-op if unchanged."""
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
    return hours == "*" or hour in hours   # ponytail: whole hours only, Task Scheduler wakes hourly at :45


def scrape(now: datetime, full: bool, setup: bool) -> None:
    """Run every scraper due at `now`; raises ScrapeError on the first failure."""
    if due(settings.members_hours, now.hour):
        print("=== Members: members page ===", flush=True)
        members.run()
    if due(settings.feed_hours, now.hour):
        print("\n=== Feed: club feed ===", flush=True)
        feed.run()   # before the leaderboard, so its runs are older than the statistics' synced_at

    weeks, synced_at, problem = {}, member_activities.now_utc(), None
    if full:
        print("\n=== MemberActivities: profile weeks ===", flush=True)
        weeks, synced_at, problem = member_activities.run(statistics.last_profile_sync(), setup=setup)
    print("\n=== Statistics: leaderboard + profiles + ledger fallback ===", flush=True)
    leaderboard = statistics.fetch_leaderboard() if full or due(settings.leaderboard_hours, now.hour) else {}
    statistics.run(weeks, leaderboard, synced_at, now.date())
    if problem:   # what the scan found is saved; failing lets the scheduler flag the gaps
        raise ScrapeError(problem)


def main() -> None:
    check_auth()
    now = datetime.now(ZoneInfo(settings.timezone))
    setup = "--setup" in sys.argv
    try:
        scrape(now, setup or "--full" in sys.argv or due(settings.member_scan_hours, now.hour), setup)
    except ScrapeError as e:
        raise SystemExit(f"scrape failed, pipeline stopped: {e}\n{REAUTH_MSG}")

    print("\n=== generate dashboard ===", flush=True)
    generate.run()

    print("\n=== publish dashboard ===", flush=True)
    publish_dashboard()

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
