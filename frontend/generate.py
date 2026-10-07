"""Generate the static site (frontend/public/index.html) from the scraped CSVs (statistics, ledger, members),
config.yaml and the nominal roll.
    python -m frontend.generate     # or via the pipeline: python -m backend.main
"""
import csv
import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backend import config
from . import renderer, stats, weather
from .names import NominalRoll

# Dashboard group tabs, decided by the roll's "Type of service" (upper-cased).
SERVING_TYPES = {"NSF", "REGULAR"}
ALUMNI_TYPES = {"NSMAN", "ALUMNI"}

# Foot sports: what the club leaderboard and statistics.csv count. The ledger also holds rides, swims, workouts...
FOOT_TYPES = {"Run", "TrailRun", "VirtualRun", "Walk", "Hike"}

# Paths are spelled out here, not imported from the scrapers, so the dashboard never loads playwright.
REPO_ROOT = Path(__file__).parent.parent
BACKEND = REPO_ROOT / "backend"
ACTIVITIES_CSV = BACKEND / "activities" / "activities.csv"
STATISTICS_CSV = BACKEND / "statistics" / "statistics.csv"
MEMBERS_CSV = BACKEND / "members" / "members.csv"
MEMBER_COUNT_CSV = BACKEND / "members" / "member_count.csv"
PUBLIC = Path(__file__).parent / "public"   # the static site GitHub Pages serves
OUT_PATH = PUBLIC / "index.html"
USER_COUNT_PATH = PUBLIC / "user-count.json"


def day_label(d) -> str:
    """The dashboard's date format: 5.9.2026."""
    return f"{d.day}.{d.month}.{d.year}"


def _local_date(iso_utc: str, tzinfo) -> str:
    """An ISO UTC timestamp as a local ``YYYY-MM-DD`` string, or "" if unusable."""
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


def _read(path: Path) -> list:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_activities(challenge_start: str, tzinfo) -> list:
    """Foot-sport rows of activities.csv whose local start date is on/after challenge_start, tagged as _date."""
    kept = []
    for r in _read(ACTIVITIES_CSV):
        d = _local_date(r.get("start_date_utc"), tzinfo)
        if d and d >= challenge_start and r.get("type") in FOOT_TYPES:
            r["_date"] = d
            kept.append(r)
    return kept


def load_daily() -> list:
    """Rows of statistics.csv: one per athlete per snapshot date, cumulative since challenge_start."""
    return _read(STATISTICS_CSV)


def latest_by_athlete(snapshots: list, day: str) -> list:
    """Per athlete, the latest snapshot dated on/before day: their cumulative totals as of then."""
    latest = {}
    for r in snapshots:
        if r["date"] <= day and r["date"] > latest.get(r["athlete_id"], {}).get("date", ""):
            latest[r["athlete_id"]] = r
    return list(latest.values())


def load_members() -> list:
    """Rows of members.csv: everyone ever on the roster, with ingest_at and left_at."""
    return _read(MEMBERS_CSV)


def load_member_counts(tzinfo) -> dict:
    """{local date: that day's last headline member count} from member_count.csv; {} if absent."""
    if not MEMBER_COUNT_CSV.exists():
        return {}
    rows = sorted(_read(MEMBER_COUNT_CSV), key=lambda r: r["scraped_at"])
    return {_local_date(r["scraped_at"], tzinfo): int(r["member_count"]) for r in rows}


def count_as_of(counts: dict, day: str) -> int | None:
    """The latest headline count dated on/before day, or None."""
    known = [d for d in counts if d <= day]
    return counts[max(known)] if known else None


def member_on(m: dict, day: str) -> bool:
    """Whether members.csv row m was in the club on day: ingested by then and not left by then."""
    left = (m.get("left_at") or "")[:10]
    return (m.get("ingest_at") or "")[:10] <= day and not (left and left <= day)


def build_grouped_data(weeks: list, acts: list, members: list, label: str, roll: NominalRoll) -> dict:
    """Split weeks/acts/members into 'all'/'serving'/'alumni' stats dicts, each tagged with label.
    Anyone off the roll lands in neither group (still counted in 'all')."""
    service_by_id = {str(m.get("athlete_id") or ""): roll.service(roll.resolve(m.get("name", "")))
                     for m in members}

    def in_group(rows, types):
        return [r for r in rows if service_by_id.get(str(r.get("athlete_id") or ""), "") in types]

    def stats_for(f_weeks, f_acts, f_members):
        return {**asdict(stats.compute_stats(f_weeks, f_acts, f_members, roll)), "label": label}

    result = {"all": stats_for(weeks, acts, members)}
    for group, types in (("serving", SERVING_TYPES), ("alumni", ALUMNI_TYPES)):
        result[group] = stats_for(in_group(weeks, types), in_group(acts, types), in_group(members, types))
    return result


def grouped_as_of(snapshots: list, acts: list, members: list, counts: dict, day: str, label: str,
                  roll: NominalRoll) -> dict:
    """build_grouped_data cumulative to day: latest snapshots, activities and members by then; the 'all' member
    total is Strava's headline as of day where one was recorded."""
    so_far = latest_by_athlete(snapshots, day)
    ran = {w["athlete_id"] for w in so_far}   # ran by then, so a member by then, whenever we first saw them
    data = build_grouped_data(so_far, [a for a in acts if a["_date"] <= day],
                              [m for m in members if member_on(m, day) or m["athlete_id"] in ran], label, roll)
    data["all"]["athlete_count"] = count_as_of(counts, day) or data["all"]["athlete_count"]
    return data


def build_daily_history(snapshots: list, acts: list, members: list, counts: dict, roll: NominalRoll,
                        today: date) -> dict:
    """{date: {date, label, all, serving, alumni}} cumulative at each snapshot date and today."""
    result = {}
    for ds in sorted({s["date"] for s in snapshots} | {today.isoformat()}):
        label = day_label(date.fromisoformat(ds))
        result[ds] = {"date": ds, "label": label,
                      **grouped_as_of(snapshots, acts, members, counts, ds, label, roll)}
    return result


def load(cfg: config.Config) -> tuple:
    """-> (snapshots, ledger foot acts >= challenge_start, members, headline counts by day, roll fitted over names)."""
    tz = ZoneInfo(cfg.timezone)
    acts = load_activities(cfg.challenge_start, tz)
    snapshots = load_daily()
    members = load_members()
    counts = load_member_counts(tz)
    roll = NominalRoll()
    # Fit once over every name: build_daily_history() re-resolves each athlete per day,
    # and a per-call match could land differently on different days.
    roll.fit({m.get("name", "") for m in members} | {a.get("athlete_name", "") for a in acts})
    print(f"Loaded {len(snapshots)} athlete-day snapshots, {len(acts)} ledger activities (>= {cfg.challenge_start}), "
          f"{len(members)} members.")
    return snapshots, acts, members, counts, roll


def build(snapshots: list, acts: list, members: list, counts: dict, roll: NominalRoll, now_dt: datetime) -> tuple:
    """-> (today_data, daily_history)."""
    today = now_dt.date().isoformat()
    data = {"today": grouped_as_of(snapshots, acts, members, counts, today, day_label(now_dt), roll)}
    return data, build_daily_history(snapshots, acts, members, counts, roll, now_dt.date())


def run() -> None:
    """load -> build -> render -> write index.html and the user-count badge."""
    cfg = config.load()
    now_dt = datetime.now(ZoneInfo(cfg.timezone))
    snapshots, acts, members, counts, roll = load(cfg)
    data, daily = build(snapshots, acts, members, counts, roll, now_dt)
    OUT_PATH.write_text(renderer.render(
        data, daily, f"{day_label(now_dt)} {now_dt.hour:02}:{now_dt.minute:02}",
        weather.weather_html(cfg.weather_lat, cfg.weather_lon, cfg.timezone),
        renderer.build_announcement_html(REPO_ROOT / cfg.announcement_path),
        cfg,
    ), encoding="utf-8")
    w = data["today"]["all"]
    USER_COUNT_PATH.write_text(json.dumps({
        "schemaVersion": 1,
        "label": "users covered",
        "message": str(w["athlete_count"]),
        "color": "blue",
    }), encoding="utf-8")

    print(f"Generated: {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.2f} MB)")
    print(f"  Cumulative: {w.get('run_count', 0)} activities, "
          f"{w.get('athlete_count', 0)} members, {w.get('total_km', 0):.0f} km")


if __name__ == "__main__":
    run()
