"""RecentActivities: the club's recent-activity feed (~2.5 days retained), the light, best-effort hourly tracker.
Appends the feed's foot activities to the ledger (activities.csv), which the dashboard adds on top of the nightly
MemberStatistics snapshot, and grows members.csv from the feed and leaderboard athletes.
"""
import json
import random

from .member_activities import FOOT_TYPES, append_activities, normalise
from ..members.members import MEMBERS_URL, parse_member_count, write_members
from ..members import members
from ..strava_session import CLUB_ID, CLUB_URL, ScrapeError, club_page, csv_column_set, require_auth

FEED_URL = f"/clubs/{CLUB_ID}/feed?feed_type=club&num_entries=100"

FETCH_JS = """async (url) => {
    const r = await fetch(url, {credentials: 'include'});
    return {ok: r.ok, status: r.status, text: await r.text()};
}"""


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
        result = page.evaluate(FETCH_JS, MEMBERS_URL)
        if not result["ok"]:
            raise ScrapeError("Session expired or blocked - re-run: python -m src.login\n"
                              f"Members page returned HTTP {result['status']}.")
        count = parse_member_count(result["text"])
        if count is None:
            raise ScrapeError("Member count not found on the members page - Strava markup may have changed.")
        return count, feed_rows(page)


def run(leaderboard: dict) -> list:
    """Feed foot activities -> ledger; members from the feed, then the leaderboard
    ({week: {athlete_id: {name, ...}}}, empty on hourly runs); returns the new members' athlete ids."""
    require_auth()
    known = csv_column_set(members.CSV_PATH, "athlete_id")
    count, rows = fetch_count_and_feed()
    new = append_activities([r for r in rows if r["type"] in FOOT_TYPES])
    print(f"feed: {len(rows)} activities, {len(new)} new foot activities -> ledger")
    athletes = {str(r["athlete_id"]): r["athlete_name"] or "" for r in rows}
    for board in leaderboard.values():
        athletes.update({aid: fig["name"] for aid, fig in board.items()})
    write_members(count, athletes)
    return sorted(csv_column_set(members.CSV_PATH, "athlete_id") - known)
