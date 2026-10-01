"""Grow the append-only members.csv from the club's recent-activity feed and leaderboard, save the
headline member count, and append the feed's foot activities to the ledger (the hourly update the
dashboard adds on top of the nightly weekly snapshot). Since 16 Sep 2026 the members page lists only admins, so only its "1055 members"
count is read; the dashboard uses it as the true total, since members who never run or rank are missed.
"""
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from ..activities.activities import FOOT_TYPES, append_activities, normalise
from ..strava_session import CLUB_ID, CLUB_URL, ScrapeError, append_new_rows, club_page, csv_column_set, require_auth

CSV_PATH = Path(__file__).parent / "members.csv"
COUNT_PATH = Path(__file__).parent / "member_count.json"
MEMBERS_URL = f"{CLUB_URL}/members"
FEED_URL = f"/clubs/{CLUB_ID}/feed?feed_type=club&num_entries=100"

FIELDS = ["athlete_id", "name", "first_seen"]

FETCH_JS = """async (url) => {
    const r = await fetch(url, {credentials: 'include'});
    return {ok: r.ok, status: r.status, text: await r.text()};
}"""


def parse_member_count(html: str) -> int | None:
    """The headline count from <span class='membership-count'>1055 members</span>, or None."""
    m = re.search(r"class=['\"]membership-count['\"][^>]*>\s*([\d,]+)\s+members?\b", html)
    return int(m.group(1).replace(",", "")) if m else None


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


def write_members(count: int, athletes: dict) -> int:
    """Save the headline count and append each athlete not in members.csv yet; returns rows appended.
    Append-only: an existing row (name snapshot, leavers) is never touched."""
    COUNT_PATH.write_text(json.dumps({"member_count": count}) + "\n", encoding="utf-8")
    seen = csv_column_set(CSV_PATH, "athlete_id")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = [{"athlete_id": aid, "name": name, "first_seen": now}
           for aid, name in athletes.items() if aid not in seen]
    append_new_rows(CSV_PATH, FIELDS, new)
    print(f"headline count {count}; {len(athletes)} feed/leaderboard athletes, {len(new)} new -> {CSV_PATH}")
    return len(new)


def scrape_members(leaderboard: dict) -> int:
    """Feed foot activities -> ledger; members from the feed, then the leaderboard
    ({week: {athlete_id: {name, ...}}}, empty on hourly runs); returns new member rows."""
    require_auth()
    count, rows = fetch_count_and_feed()
    new = append_activities([r for r in rows if r["type"] in FOOT_TYPES])
    print(f"feed: {len(rows)} activities, {len(new)} new foot activities -> ledger")
    athletes = {str(r["athlete_id"]): r["athlete_name"] or "" for r in rows}
    for board in leaderboard.values():
        athletes.update({aid: fig["name"] for aid, fig in board.items()})
    return write_members(count, athletes)
