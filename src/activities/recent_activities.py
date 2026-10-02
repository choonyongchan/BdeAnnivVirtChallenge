"""RecentActivities: the club's recent-activity feed (~2.5 days retained), the light, best-effort hourly tracker.
Appends the feed's foot activities to the ledger (activities.csv), which the dashboard adds on top of the nightly
MemberStatistics snapshot, and grows members.csv from the feed and leaderboard athletes (RecentActivityMembers,
LeaderboardMembers).
"""
import json
import random

from .member_activities import FOOT_TYPES, append_activities, normalise
from ..members.members import LeaderboardMembers, RecentActivityMembers, fetch_member_count, write_members
from ..strava_session import CLUB_ID, CLUB_URL, FETCH_JS, ScrapeError, club_page, require_auth

FEED_URL = f"/clubs/{CLUB_ID}/feed?feed_type=club&num_entries=100"


def feed_rows(page) -> list:
    """Every activity row in the club feed (~2.5 days retained), following the cursor."""
    out, url = [], FEED_URL
    for _ in range(50):  # circuit breaker; the feed's whole retention is a handful of pages
        result = page.evaluate(FETCH_JS, url)
        try:
            data = json.loads(result["text"])
        except ValueError:
            raise ScrapeError("Session expired or blocked - re-run: python -m src.login\n"
                              f"Feed response was not JSON: {result['text'][:120]!r}")
        entries = data.get("entries") or []
        out += [r for e in entries for r in normalise(e) if r["activity_id"] and r["athlete_id"]]
        if not entries or not (data.get("pagination") or {}).get("hasMore"):
            return out
        cursor = entries[-1]["cursorData"]
        url = f"{FEED_URL}&before={cursor['updated_at']}&cursor={cursor['rank']}"
        page.wait_for_timeout(random.randint(400, 900))
    return out


def fetch_count_and_feed() -> tuple:
    """-> (headline member count, the feed's activity rows), one browser session."""
    with club_page(f"{CLUB_URL}/recent_activity") as page:
        return fetch_member_count(page), feed_rows(page)


def run(leaderboard: dict) -> list:
    """Feed foot activities -> ledger; members from the feed, then the leaderboard
    ({week: {athlete_id: {name, ...}}}, empty on hourly runs); returns the new members' athlete ids."""
    require_auth()
    count, rows = fetch_count_and_feed()
    new = append_activities([r for r in rows if r["type"] in FOOT_TYPES])
    print(f"feed: {len(rows)} activities, {len(new)} new foot activities -> ledger")
    return sorted(write_members(count, [RecentActivityMembers(rows), LeaderboardMembers(leaderboard)]))
