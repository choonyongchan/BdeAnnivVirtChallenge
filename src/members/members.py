"""Scrape the club's full member list from Strava's logged-in members page into an
append-only CSV ledger.

The public club API (getClubMembersByClubId) was deactivated by Strava in 2026, so
this pages through the same HTML the members page renders for itself.

Run via the pipeline (python -m src.main); the one-off login is python -m src.login.

Shares src/auth_state.json with the activity scraper - one login covers both (browser
session, login, and retry live in src/strava_session.py). The ledger is append-only:
each run appends a first_seen row for every athlete_id not already in it and never
touches an existing row, so name is a first-seen snapshot that may drift from Strava
and a member who leaves simply stops getting new rows (their row stays).
"""
import csv
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from ..strava_session import CLUB_ID, ScrapeError, StravaScraper

CSV_PATH = Path(__file__).parent / "members.csv"
MEMBERS_URL = f"https://www.strava.com/clubs/{CLUB_ID}/members?page={{page}}"

FIELDS = ["athlete_id", "name", "first_seen"]


def parse_members(html: str) -> list:
    """One members-page response -> [(athlete_id, name)]. The page has a couple of
    <ul class='list-athletes'> blocks (a small club-admins one plus the paginated member
    grid); the athlete-anchor pattern is specific enough to scan the whole page directly
    and let the caller dedupe by id."""
    return [(aid, name.strip()) for aid, name in
            re.findall(r'href="/athletes/(\d+)"[^>]*>([^<]{1,80})</a>', html)
            if name.strip()]


class MemberScraper(StravaScraper):
    """Scrape the full club member list into the append-only members.csv ledger."""

    landing_url = f"https://www.strava.com/clubs/{CLUB_ID}"

    def fetch(self) -> dict:
        """Page through the members list from inside the club page; return {athlete_id: name}.
        Stop when a page adds no new athlete ids."""
        with self._club_page() as page:
            members = {}
            empty_streak = 0
            for n in range(1, 61):  # safety ceiling; the club is ~23 pages
                result = page.evaluate(
                    """async (url) => {
                        const r = await fetch(url, {credentials: 'include'});
                        return {ok: r.ok, status: r.status, text: await r.text()};
                    }""",
                    MEMBERS_URL.format(page=n),
                )
                if not result["ok"]:
                    raise ScrapeError("Session expired or blocked - re-run: python -m src.login\n"
                                      f"Page {n} returned HTTP {result['status']}.")
                rows = parse_members(result["text"])
                new = {aid: name for aid, name in rows if aid not in members}
                print(f"page {n}: {len(rows)} rows, {len(new)} new, {len(members) + len(new)} total")
                members.update(new)
                # Stop only after two consecutive dry pages, so a single transiently
                # empty or failed-to-parse page does not truncate the crawl early.
                empty_streak = 0 if new else empty_streak + 1
                if empty_streak >= 2:
                    break
                page.wait_for_timeout(random.randint(400, 900))

        if not members:
            raise ScrapeError("No members parsed - Strava markup may have changed, or session expired.")
        return members

    def write(self, current: dict) -> int:
        """Append a first_seen row for every athlete_id not already in the ledger; existing
        rows are never touched, so name is a first-seen snapshot that may drift from Strava."""
        seen = set()
        if CSV_PATH.exists():
            with CSV_PATH.open(encoding="utf-8", newline="") as f:
                seen = {r["athlete_id"] for r in csv.DictReader(f)}

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new = [{"athlete_id": aid, "name": name, "first_seen": now}
               for aid, name in current.items() if aid not in seen]

        write_header = not CSV_PATH.exists()
        with CSV_PATH.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if write_header:
                w.writeheader()
            w.writerows(new)

        print(f"{len(current)} current members, {len(new)} new -> {CSV_PATH}")
        return len(new)
