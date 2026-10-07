"""What the scrapers (backend) write and the dashboard (frontend) reads: the CSV ledgers' paths, the foot sports
that count, CSV read/write helpers and timestamp helpers. Imports nothing heavy, so the dashboard never loads
playwright."""
import csv
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).parent.parent / "backend"
ACTIVITIES_CSV = BACKEND / "activities" / "activities.csv"
STATISTICS_CSV = BACKEND / "statistics" / "statistics.csv"
MEMBERS_CSV = BACKEND / "members" / "members.csv"
MEMBER_COUNT_CSV = BACKEND / "members" / "member_count.csv"

# Foot sports: what the club leaderboard and statistics.csv count. The ledger also holds rides, swims, workouts...
FOOT_TYPES = {"Run", "TrailRun", "VirtualRun", "Walk", "Hike"}


def read_csv(path: Path) -> list:
    """Every row of the CSV at path as a dict; [] if the file doesn't exist yet."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def append_new_rows(path: Path, fields: list, rows: list) -> None:
    """Append rows to the CSV at path, writing the header first if the file is new."""
    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            w.writeheader()
        w.writerows(rows)


def write_csv(path: Path, fields: list, rows: list) -> None:
    """Replace the CSV at path with rows, via a temp file so a crash never leaves it half-written."""
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def now_utc() -> str:
    """Now as an ISO UTC timestamp to the second: the scrapers' scraped_at / ingest_at / synced_at stamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_date(iso_utc: str, tzinfo) -> str:
    """An ISO UTC timestamp (naive = UTC) as a local YYYY-MM-DD string, or "" if unusable."""
    raw = (iso_utc or "").strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tzinfo).date().isoformat()
