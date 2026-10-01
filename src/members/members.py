"""Keep the append-only members.csv ledger (grown from activities.csv) and the headline member count.
Since 16 Sep 2026 the members page lists only admins, so only its "1055 members" count is read.
"""
import json
import re
from pathlib import Path

from ..activities.activities import CSV_PATH as ACTIVITIES_CSV
from ..strava_session import (CLUB_URL, ScrapeError, StravaScraper, append_new_rows, club_page,
                              csv_column_set, read_csv)

CSV_PATH = Path(__file__).parent / "members.csv"
COUNT_PATH = Path(__file__).parent / "member_count.json"
MEMBERS_URL = f"{CLUB_URL}/members"

FIELDS = ["athlete_id", "name", "first_seen"]


def parse_member_count(html: str) -> int | None:
    """The headline count from <span class='membership-count'>1055 members</span>, or None."""
    m = re.search(r"class=['\"]membership-count['\"][^>]*>\s*([\d,]+)\s+members?\b", html)
    return int(m.group(1).replace(",", "")) if m else None


def activity_athletes(path: Path) -> dict:
    """{athlete_id: (name, first start_date_utc)} for every athlete in activities.csv."""
    out = {}
    for r in read_csv(path):
        aid, start = r.get("athlete_id") or "", r.get("start_date_utc") or ""
        if aid and (aid not in out or start < out[aid][1]):
            out[aid] = (r.get("athlete_name", ""), start)
    return out


class MemberScraper(StravaScraper):
    """Read the club's headline member count and grow members.csv from activities.csv."""

    def fetch(self) -> int:
        """Fetch the members page from inside the club page; return its headline count."""
        with club_page(self.landing_url) as page:
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
        """Save the headline count, then append each new activities.csv athlete; returns rows appended.
        Append-only: an existing row (name snapshot, leavers) is never touched."""
        COUNT_PATH.write_text(json.dumps({"member_count": count}) + "\n", encoding="utf-8")

        seen = csv_column_set(CSV_PATH, "athlete_id")
        new = [{"athlete_id": aid, "name": name, "first_seen": start.replace("Z", "+00:00")}
               for aid, (name, start) in activity_athletes(ACTIVITIES_CSV).items()
               if aid not in seen]
        append_new_rows(CSV_PATH, FIELDS, new)

        print(f"headline count {count}; {len(new)} new members from activities -> {CSV_PATH}")
        return len(new)
