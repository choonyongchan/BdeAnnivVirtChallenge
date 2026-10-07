"""Feed: the club's recent-activity feed, the light hourly tracker. Strava keeps about a day of it (~26 h,
whatever the page size), so an hourly run has a day of slack. Every activity in it, any sport, goes into the ledger
(activities.csv); the nightly MemberActivities scan fills whatever the feed missed.
"""
import json
import random

from .member_activities import append_activities, normalise
from ..strava_session import CLUB_ID, CLUB_URL, FETCH_JS, ScrapeError, club_page, require_auth

FEED_URL = f"/clubs/{CLUB_ID}/feed?feed_type=club&num_entries=100"


def feed_rows(page) -> list:
    """Every activity row in the club feed, following the cursor."""
    out, url = [], FEED_URL
    for _ in range(50):  # circuit breaker; the feed's whole retention is a page or two
        result = page.evaluate(FETCH_JS, url)
        try:
            data = json.loads(result["text"])
        except ValueError:
            raise ScrapeError("Session expired or blocked - re-run: python -m backend.login\n"
                              f"Feed response was not JSON: {result['text'][:120]!r}")
        entries = data.get("entries") or []
        out += [r for e in entries for r in normalise(e) if r["activity_id"] and r["athlete_id"]]
        if not entries or not (data.get("pagination") or {}).get("hasMore"):
            return out
        cursor = entries[-1]["cursorData"]
        url = f"{FEED_URL}&before={cursor['updated_at']}&cursor={cursor['rank']}"
        page.wait_for_timeout(random.randint(400, 900))
    return out


def fetch_feed() -> list:
    """The feed's activity rows, one browser session."""
    with club_page(f"{CLUB_URL}/recent_activity") as page:
        return feed_rows(page)


def run() -> int:
    """Feed activities -> ledger; returns how many were new."""
    require_auth()
    rows = fetch_feed()
    new = append_activities(rows)
    print(f"feed: {len(rows)} activities, {len(new)} new -> ledger")
    return len(new)
