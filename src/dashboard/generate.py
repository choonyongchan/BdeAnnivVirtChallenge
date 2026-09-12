"""Generate the static repo-root index.html from the scraped CSVs.

    python -m src.dashboard.generate     # or via the pipeline: python -m src.main

Reads src/config.yaml plus:
  src/activities/activities.csv   - one row per activity (real activity_id, start_date_utc)
  src/members/members.csv         - one row per club member (real athlete_id, first_seen)
  src/nominal_roll/nominal_roll.csv - the roster (via names.NominalRoll)

Replaces the src_bak Strava-API + JSON-ledger + start-anchor pipeline: the CSVs
already carry unique ids and real activity times, so activities are simply
filtered to start_date_utc >= challenge_start and history is replayed from the
real per-day dates.
"""
import csv
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import config
from . import renderer, stats, weather
from .names import NominalRoll

# Dashboard group tabs, decided by the roll's "Type of service" (upper-cased).
SERVING_TYPES = {"NSF", "REGULAR"}
ALUMNI_TYPES = {"NSMAN", "ALUMNI"}

REPO_ROOT = Path(__file__).parent.parent.parent
ACTIVITIES_CSV = Path(__file__).parent.parent / "activities" / "activities.csv"
MEMBERS_CSV = Path(__file__).parent.parent / "members" / "members.csv"
OUT_PATH = REPO_ROOT / "index.html"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> config.Config:
    """Shared settings (src/config.py) with announcement_path resolved to a path."""
    cfg = config.load()
    cfg.announcement_path = REPO_ROOT / cfg.announcement_path
    return cfg


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _zone(tz: str) -> ZoneInfo:
    return ZoneInfo(tz)


def day_label(d) -> str:
    """The dashboard's date format: 5.9.2026."""
    return f"{d.day}.{d.month}.{d.year}"


def _local_date(iso_utc: str, tzinfo) -> str:
    """An activity's start_date_utc as a local ``YYYY-MM-DD`` string, or "" if unusable."""
    raw = (iso_utc or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tzinfo).date().isoformat()


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_activities(challenge_start: str, tzinfo) -> list:
    """Rows of activities.csv whose local start date is on/after challenge_start."""
    with open(ACTIVITIES_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    kept = []
    for r in rows:
        d = _local_date(r.get("start_date_utc"), tzinfo)
        if d and d >= challenge_start:
            r["_date"] = d
            kept.append(r)
    return kept


def load_members() -> list:
    """Rows of members.csv (the whole append-only club roster)."""
    with open(MEMBERS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Grouping / history
# ---------------------------------------------------------------------------

def build_grouped_data(acts: list, members: list, label: str, roll: NominalRoll) -> dict:
    """Split acts/members into 'all'/'serving'/'alumni' stats dicts, each tagged
    with label. serving = SERVING_TYPES, alumni = ALUMNI_TYPES; anyone off the
    roll lands in neither group (still counted in 'all')."""
    service_by_id = {
        str(m.get("athlete_id") or ""): roll.service(roll.resolve(m.get("name", "")))
        for m in (members or [])
    }

    def member_service(m):
        return roll.service(roll.resolve(m.get("name", "")))

    def act_service(a):
        return service_by_id.get(str(a.get("athlete_id") or ""), "")

    def stats_for(f_acts, f_members):
        s = stats.compute_stats(f_acts, members=f_members, roll=roll)
        return {**s.to_dict(), "label": label}

    result = {"all": stats_for(acts, members)}
    for group, types in (("serving", SERVING_TYPES), ("alumni", ALUMNI_TYPES)):
        result[group] = stats_for(
            [a for a in acts if act_service(a) in types],
            [m for m in (members or []) if member_service(m) in types],
        )
    return result


def build_daily_history(acts: list, members: list, roll: NominalRoll, tzinfo) -> dict:
    """{date: {date, label, all, serving, alumni}} for every date that saw an
    activity, recomputing cumulative stats over everything up to that date.

    Uses each activity's real local start date (not scrape time), and the
    members whose first_seen is on/before that date.
    """
    dated = [(a, a.get("_date") or _local_date(a.get("start_date_utc"), tzinfo)) for a in acts]
    dated = [(a, d) for a, d in dated if d]
    result = {}
    for ds in sorted({d for _, d in dated}):
        subset = [a for a, d in dated if d <= ds]
        day_members = [m for m in members if (m.get("first_seen", "")[:10] <= ds)]
        label = day_label(datetime.fromisoformat(ds).date())
        groups = build_grouped_data(subset, day_members, label, roll)
        for bucket in groups.values():
            renderer._slim_leaderboard(bucket)
        result[ds] = {"date": ds, "label": label, **groups}
    return result


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

class DashboardGenerator:
    """One load -> compute -> render -> write pass for the dashboard."""

    def __init__(self, cfg: config.Config):
        """Bind config and build the shared roll and timezone."""
        self.cfg = cfg
        #: tzinfo for every local-date decision in this run.
        self.tzinfo = _zone(cfg.timezone)
        #: Roster lookup, loaded once and reused for every group/day.
        self.roll = NominalRoll()
        #: activities.csv rows kept for the challenge (set by load()).
        self.acts: list = []
        #: members.csv rows, the whole club roster (set by load()).
        self.members: list = []

    def load(self) -> None:
        """Read activities.csv (>= challenge_start) and the full members.csv."""
        self.acts = load_activities(self.cfg.challenge_start, self.tzinfo)
        self.members = load_members()
        print(f"Loaded {len(self.acts)} activities (>= {self.cfg.challenge_start}), "
              f"{len(self.members)} members.")

    def build(self, now_dt: datetime) -> tuple:
        """-> (today_data, daily_history) computed from the loaded rows."""
        data = {"today": build_grouped_data(
            self.acts, self.members, day_label(now_dt), self.roll)}
        daily = build_daily_history(self.acts, self.members, self.roll, self.tzinfo)
        return data, daily

    def render(self, data: dict, daily: dict, now_dt: datetime) -> str:
        """Fill the page template from the computed data and return the HTML string."""
        human_label = f"{day_label(now_dt)} {now_dt.hour:02}:{now_dt.minute:02}"
        return renderer.render(
            data, daily, human_label,
            weather.weather_html(self.cfg.weather_lat, self.cfg.weather_lon, self.cfg.timezone),
            renderer.build_announcement_html(self.cfg.announcement_path),
            self.cfg.club_name, self.cfg.club_id,
        )

    def run(self) -> None:
        """load -> build -> render -> write OUT_PATH, with the summary prints."""
        self.load()
        now_dt = datetime.now(self.tzinfo)
        data, daily = self.build(now_dt)
        OUT_PATH.write_text(self.render(data, daily, now_dt), encoding="utf-8")

        w = data["today"]["all"]
        print(f"Generated: {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.2f} MB)")
        print(f"  Cumulative: {w.get('run_count', 0)} activities, "
              f"{w.get('athlete_count', 0)} members, {w.get('total_km', 0):.0f} km")


def run():
    """Build the dashboard from config.yaml — the module entry point."""
    DashboardGenerator(load_config()).run()


if __name__ == "__main__":
    run()
