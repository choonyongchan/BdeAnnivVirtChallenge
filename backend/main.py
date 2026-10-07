"""Scrape what config.yaml's schedule says is due this hour, then generate the dashboard; the first failure stops
it. scripts/run_pipeline.ps1 commits and pushes the result.
Hourly:  Members (members page -> members.csv, member_count.csv), Feed (club feed -> activities.csv),
         Statistics (leaderboard + ledger fallback -> statistics.csv).
Nightly (member_scan hours, or --full): MemberActivities too - every current member's last two profile weeks, every
         week since challenge_start for members ingested since the last scan (--setup: everyone) -> activities.csv
         gaps and profile figures in statistics.csv.
    python -m backend.main [--full] [--setup]
"""
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from frontend import generate
from shared.config import settings
from shared.data import now_utc

from .activities import feed, member_activities
from .members import members
from .statistics import statistics
from .strava_session import AUTH_PATH, ScrapeError

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

    weeks, synced_at, problem = {}, now_utc(), None
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

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
