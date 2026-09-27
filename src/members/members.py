"""Keep the append-only members.csv ledger and the club's headline member count.

Since 16 Sep 2026 Strava's members page lists only the club admins, not the members,
so the ledger grows from activities.csv instead: every athlete who has logged a club
activity but has no ledger row gets one, with first_seen = their first activity.
Members who have not run yet therefore have no row; the club's true total comes from
the members page's headline count ("1055 members"), saved to member_count.json.

Run via the pipeline (python -m src.main), after the activity scrape; the one-off login
is python -m src.login. Shares src/auth_state.json with the activity scraper (browser
session, login, and retry live in src/strava_session.py). The ledger is append-only:
an existing row is never touched, so name is a first-seen snapshot that may drift from
Strava and a member who leaves keeps their row.
"""
import csv
import json
import re
from pathlib import Path

from ..activities.activities import CSV_PATH as ACTIVITIES_CSV
from ..strava_session import CLUB_ID, ScrapeError, StravaScraper, append_new_rows, csv_column_set

CSV_PATH = Path(__file__).parent / "members.csv"
COUNT_PATH = Path(__file__).parent / "member_count.json"
MEMBERS_URL = f"https://www.strava.com/clubs/{CLUB_ID}/members"

FIELDS = ["athlete_id", "name", "first_seen"]


def parse_member_count(html: str) -> int | None:
    """The club's headline member count (<span class='membership-count'>1055 members</span>),
    or None if the page no longer has it."""
    m = re.search(r"class=['\"]membership-count['\"][^>]*>\s*([\d,]+)\s+members?\b", html)
    return int(m.group(1).replace(",", "")) if m else None


def activity_athletes(path: Path) -> dict:
    """{athlete_id: (name, first start_date_utc)} for every athlete in activities.csv."""
    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            aid, start = r.get("athlete_id") or "", r.get("start_date_utc") or ""
            if aid and (aid not in out or start < out[aid][1]):
                out[aid] = (r.get("athlete_name", ""), start)
    return out


class MemberScraper(StravaScraper):
    """Read the club's headline member count and grow members.csv from activities.csv."""

    landing_url = f"https://www.strava.com/clubs/{CLUB_ID}"

    def fetch(self) -> int:
        """Fetch the members page from inside the club page; return its headline count."""
        with self._club_page() as page:
            result = page.evaluate(
                """async (url) => {
                    const r = await fetch(url, {credentials: 'include'});
                    return {ok: r.ok, status: r.status, text: await r.text()};
                }""",
                MEMBERS_URL,
            )
        if not result["ok"]:
            raise ScrapeError("Session expired or blocked - re-run: python -m src.login\n"
                              f"Members page returned HTTP {result['status']}.")
        count = parse_member_count(result["text"])
        if count is None:
            raise ScrapeError("Member count not found on the members page - "
                              "Strava markup may have changed.")
        return count

    def write(self, count: int) -> int:
        """Save the headline count, then append a row (first_seen = first activity) for
        every activities.csv athlete not already in the ledger. Returns rows appended."""
        COUNT_PATH.write_text(json.dumps({"member_count": count}) + "\n", encoding="utf-8")

        seen = csv_column_set(CSV_PATH, "athlete_id")
        new = [{"athlete_id": aid, "name": name, "first_seen": start.replace("Z", "+00:00")}
               for aid, (name, start) in activity_athletes(ACTIVITIES_CSV).items()
               if aid not in seen]
        append_new_rows(CSV_PATH, FIELDS, new)

        print(f"headline count {count}; {len(new)} new members from activities -> {CSV_PATH}")
        return len(new)
