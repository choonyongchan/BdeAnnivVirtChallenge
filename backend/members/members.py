"""members.csv, the append-only union of club members from three sources, and the club's headline member count
(member_count.json), which the dashboard shows as the total.
    NominalRollMembers     roll STRAVA usernames that Strava's athlete search shows in this club (nightly)
    RecentActivityMembers  athletes in the club's recent-activity feed
    LeaderboardMembers     athletes on the club leaderboard (this and last week; nightly)
Since 16 Sep 2026 the members page lists only admins, so only its "1055 members" headline is read.
"""
import csv
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from ..strava_session import (CLUB_URL, FETCH_JS, ScrapeError, append_new_rows, club_page, csv_column_set,
                              read_csv, require_auth)

CSV_PATH = Path(__file__).parent / "members.csv"
COUNT_PATH = Path(__file__).parent / "member_count.json"
ROLL_PATH = Path(__file__).parent.parent / "nominal_roll" / "nominal_roll.csv"
MEMBERS_URL = f"{CLUB_URL}/members"
SEARCH_URL = "/athletes/search?text={}"

FIELDS = ["athlete_id", "name", "first_seen"]


def parse_member_count(html: str) -> int | None:
    """The headline count from <span class='membership-count'>1055 members</span>, or None."""
    m = re.search(r"class=['\"]membership-count['\"][^>]*>\s*([\d,]+)\s+members?\b", html)
    return int(m.group(1).replace(",", "")) if m else None


def fetch_member_count(page) -> int:
    """The members page's headline count, fetched inside a logged-in club page."""
    result = page.evaluate(FETCH_JS, MEMBERS_URL)
    if not result["ok"]:
        raise ScrapeError("Session expired or blocked - re-run: python -m backend.login\n"
                          f"Members page returned HTTP {result['status']}.")
    count = parse_member_count(result["text"])
    if count is None:
        raise ScrapeError("Member count not found on the members page - Strava markup may have changed.")
    return count


def parse_club_name(title: str) -> str | None:
    """The club's Strava name from a club page title: 'Singapore Club | BDE ... CHALLENGE on Strava'."""
    m = re.search(r"\|\s*(.+?)\s+on Strava\s*$", title)
    return m.group(1) if m else None


def parse_search(html: str) -> list | None:
    """The athlete search page's results (its __NEXT_DATA__ JSON), or None if the page has none."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    return json.loads(m.group(1))["props"]["pageProps"].get("searchResults") if m else None


def club_hit(results: list, club: str) -> dict | None:
    """The first result Strava says shares this club with us ('You and Loo are both in <club>'), or None."""
    return next((r for r in results if r.get("analyticReasonCategory") == "common_club"
                 and club.lower() in (r.get("subtitle") or "").lower()), None)


def same_name(name: str) -> str:
    """A name compared ignoring case and repeated spaces: 'Loo  Jia Jun' == 'loo jia jun'."""
    return " ".join(name.split()).lower()


class RecentActivityMembers:
    """Athletes behind the club feed's activity rows."""

    def __init__(self, feed_rows: list):
        self.athletes = {str(r["athlete_id"]): r["athlete_name"] or "" for r in feed_rows}


class LeaderboardMembers:
    """Athletes on the club leaderboard, {week: {athlete_id: {name, ...}}} (empty on hourly runs)."""

    def __init__(self, leaderboard: dict):
        self.athletes = {aid: fig["name"] for board in leaderboard.values() for aid, fig in board.items()}


class NominalRollMembers:
    """Roll STRAVA usernames confirmed by Strava's athlete search: the first result in this club, whatever its name.
    Only usernames not already a members.csv name are searched; a failed search stops the rest, keeping the hits."""

    def __init__(self, page, club: str):
        known = {same_name(r["name"]) for r in read_csv(CSV_PATH)}
        with ROLL_PATH.open(encoding="utf-8-sig", newline="") as f:
            usernames = {same_name(r["STRAVA username"]) for r in csv.DictReader(f)} - known - {""}
        self.searched = 0
        self.athletes = {}
        for username in sorted(usernames):
            result = page.evaluate(FETCH_JS, SEARCH_URL.format(quote(username)))
            results = parse_search(result["text"]) if result["ok"] else None
            if results is None:
                print(f"athlete search failed (HTTP {result['status']}) after {self.searched} of {len(usernames)}; "
                      "the next nightly run retries")
                break
            self.searched += 1
            hit = club_hit(results, club)
            if hit:
                self.athletes[hit["idStr"]] = hit["name"]
            page.wait_for_timeout(random.randint(400, 900))


def add_nominal_roll() -> list:
    """Search the roll's unmatched usernames and append the club members found; returns the new athlete ids."""
    require_auth()
    with club_page(MEMBERS_URL) as page:
        club = parse_club_name(page.title())
        if not club:
            raise ScrapeError(f"Club name not found in the members page title {page.title()!r} - "
                              "session expired or Strava markup changed.")
        roll = NominalRollMembers(page, club)
    new = append_members(roll.athletes)
    print(f"roll: {roll.searched} usernames searched, {len(roll.athletes)} in {club}, {len(new)} new -> {CSV_PATH}")
    return new


def append_members(athletes: dict) -> list:
    """Append each athlete not in members.csv yet; returns their athlete ids.
    Append-only: an existing row (name snapshot, leavers) is never touched."""
    seen = csv_column_set(CSV_PATH, "athlete_id")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = [{"athlete_id": aid, "name": name, "first_seen": now}
           for aid, name in athletes.items() if aid not in seen]
    append_new_rows(CSV_PATH, FIELDS, new)
    return [r["athlete_id"] for r in new]


def write_members(count: int, sources: list) -> list:
    """Save the headline count and append the union of the sources' athletes; returns the new athlete ids."""
    COUNT_PATH.write_text(json.dumps({"member_count": count}) + "\n", encoding="utf-8")
    athletes = {aid: name for source in sources for aid, name in source.athletes.items()}
    new = append_members(athletes)
    print(f"headline count {count}; {len(athletes)} feed/leaderboard athletes, {len(new)} new -> {CSV_PATH}")
    return new
