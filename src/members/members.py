"""The append-only members.csv and the headline member count, grown by RecentActivities from the club feed and
leaderboard. Since 16 Sep 2026 the members page lists only admins, so only its "1055 members" count is read; the
dashboard uses it as the true total, since members who never run or rank are missed.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..strava_session import CLUB_URL, append_new_rows, csv_column_set

CSV_PATH = Path(__file__).parent / "members.csv"
COUNT_PATH = Path(__file__).parent / "member_count.json"
MEMBERS_URL = f"{CLUB_URL}/members"

FIELDS = ["athlete_id", "name", "first_seen"]


def parse_member_count(html: str) -> int | None:
    """The headline count from <span class='membership-count'>1055 members</span>, or None."""
    m = re.search(r"class=['\"]membership-count['\"][^>]*>\s*([\d,]+)\s+members?\b", html)
    return int(m.group(1).replace(",", "")) if m else None


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
