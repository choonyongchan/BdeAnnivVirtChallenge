"""Scrape the club's full member list from Strava's logged-in members page into an
append-only CSV ledger.

The public club API (getClubMembersByClubId) was deactivated by Strava in 2026, so
this pages through the same HTML the members page renders for itself.

Run via the pipeline (python -m src.main); the one-off login is python -m src.login.

Shares src/auth_state.json with the activity scraper - one login covers both (browser
session, login, and retry live in src/strava_session.py). The ledger keeps every athlete
ever seen: new rows get first_seen, every row's last_seen is bumped on each run, and rows
are never deleted, so a member who leaves keeps a frozen last_seen.
"""
import csv
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from ..strava_session import CLUB_ID, ScrapeError, StravaScraper

CSV_PATH = Path(__file__).parent / "members.csv"
MEMBERS_URL = f"https://www.strava.com/clubs/{CLUB_ID}/members?page={{page}}"

FIELDS = ["athlete_id", "name", "first_seen", "last_seen"]


def parse_members(html: str) -> list:
    """One members-page response -> [(athlete_id, name)]. The page has a couple of
    <ul class='list-athletes'> blocks (a small club-admins one plus the paginated member
    grid); take anchors from all of them and let the caller dedupe by id."""
    out = []
    for block in re.findall(r"<ul class='list-athletes'>(.*?)</ul>", html, re.S):
        for aid, name in re.findall(r'href="/athletes/(\d+)"[^>]*>([^<]{1,80})</a>', block):
            name = name.strip()
            if name:
                out.append((aid, name))
    return out


class MemberScraper(StravaScraper):
    """Scrape the full club member list into the append-only members.csv ledger."""

    landing_url = f"https://www.strava.com/clubs/{CLUB_ID}"

    def fetch(self) -> dict:
        """Page through the members list from inside the club page; return {athlete_id: name}.
        Stop when a page adds no new athlete ids."""
        with self._club_page() as page:
            members = {}
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
                if not new:
                    break
                members.update(new)
                page.wait_for_timeout(random.randint(400, 900))

        if not members:
            raise ScrapeError("No members parsed - Strava markup may have changed, or session expired.")
        return members

    def write(self, current: dict) -> None:
        """Bump last_seen for known athletes, add first_seen rows for new ones, never delete."""
        ledger = {}
        if CSV_PATH.exists():
            with CSV_PATH.open(encoding="utf-8", newline="") as f:
                ledger = {r["athlete_id"]: r for r in csv.DictReader(f)}

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        added = 0
        for aid, name in current.items():
            row = ledger.get(aid)
            if row:
                row["name"] = name
                row["last_seen"] = now
            else:
                ledger[aid] = {"athlete_id": aid, "name": name, "first_seen": now, "last_seen": now}
                added += 1

        rows = sorted(ledger.values(), key=lambda r: r["name"].lower())
        with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)

        left = len(ledger) - len(current)
        print(f"{len(current)} current members, {added} new, {left} in ledger no longer listed "
              f"-> {CSV_PATH}")
