"""Generate the static repo-root index.html from the scraped CSVs (daily week-to-date snapshots, ledger, members), config.yaml and the nominal roll.
    python -m src.dashboard.generate     # or via the pipeline: python -m src.main
"""
import csv
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import config
from . import renderer, stats, weather
from .names import NominalRoll

# Dashboard group tabs, decided by the roll's "Type of service" (upper-cased).
SERVING_TYPES = {"NSF", "REGULAR"}
ALUMNI_TYPES = {"NSMAN", "ALUMNI"}

# Paths are spelled out here, not imported from the scrapers, so the dashboard never loads playwright.
REPO_ROOT = Path(__file__).parent.parent.parent
ACTIVITIES_CSV = Path(__file__).parent.parent / "activities" / "activities.csv"
DAILY_CSV = Path(__file__).parent.parent / "activities" / "daily.csv"
MEMBERS_CSV = Path(__file__).parent.parent / "members" / "members.csv"
MEMBER_COUNT_JSON = Path(__file__).parent.parent / "members" / "member_count.json"
OUT_PATH = REPO_ROOT / "index.html"
USER_COUNT_PATH = REPO_ROOT / "src" / "user-count.json"


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


def load_activities(challenge_start: str, tzinfo) -> list:
    """Rows of activities.csv whose local start date is on/after challenge_start, tagged with it as _date."""
    with open(ACTIVITIES_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    kept = []
    for r in rows:
        d = _local_date(r.get("start_date_utc"), tzinfo)
        if d and d >= challenge_start:
            r["_date"] = d
            kept.append(r)
    return kept


def load_daily() -> list:
    """Rows of daily.csv: one per athlete per snapshot date, cumulative over that date's Mon-Sun week (week = the Monday)."""
    with open(DAILY_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def latest_by_week(snapshots: list, day: str) -> list:
    """Per athlete per week, the latest snapshot dated on/before day: the week's totals as of then."""
    latest = {}
    for r in snapshots:
        key = (r["athlete_id"], r["week"])
        if r["date"] <= day and r["date"] > latest.get(key, {}).get("date", ""):
            latest[key] = r
    return list(latest.values())


def feed_updates(snapshots: list, acts: list) -> list:
    """One row per ledger activity scraped after its athlete's latest snapshot (synced_at): the hourly club
    feed since the nightly scan, and runs only the feed can see (private profiles). Per athlete, not per week,
    so Strava's and our week edges can't double-count."""
    synced = {}
    for w in snapshots:
        synced[w["athlete_id"]] = max(synced.get(w["athlete_id"], ""), w.get("synced_at") or "")
    extra = []
    for a in acts:
        if (a.get("scraped_at") or "") > synced.get(a["athlete_id"], ""):
            d = date.fromisoformat(a["_date"])
            extra.append({"athlete_id": a["athlete_id"], "date": a["_date"],
                          "week": (d - timedelta(days=d.weekday())).isoformat(),
                          "distance_m": a["distance_m"], "moving_time_s": a["moving_time_s"],
                          "elev_gain_m": a["elev_gain_m"], "activities": 1, "source": "feed"})
    return extra


def load_members() -> list:
    """Rows of members.csv (the whole append-only club roster)."""
    with open(MEMBERS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_member_count() -> int | None:
    """Strava's headline member count, or None if member_count.json is absent."""
    if not MEMBER_COUNT_JSON.exists():
        return None
    return json.loads(MEMBER_COUNT_JSON.read_text(encoding="utf-8"))["member_count"]


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


def weeks_as_of(snapshots: list, extras: list, day: str) -> list:
    """Week rows cumulative to day: each week's latest snapshot by then, plus feed rows dated by then."""
    return latest_by_week(snapshots, day) + [e for e in extras if e["date"] <= day]


def build_daily_history(snapshots: list, extras: list, acts: list, members: list, roll: NominalRoll,
                        today: date) -> dict:
    """{date: {date, label, all, serving, alumni}} cumulative at each snapshot date and today.
    Snapshots count as of their date, feed rows and ledger activities by local start date, members by first_seen."""
    result = {}
    for ds in sorted({s["date"] for s in snapshots} | {today.isoformat()}):
        label = day_label(date.fromisoformat(ds))
        so_far = weeks_as_of(snapshots, extras, ds)
        ran = {w["athlete_id"] for w in so_far}   # ran by then, so a member by then, whenever we first saw them
        result[ds] = {"date": ds, "label": label, **build_grouped_data(
            so_far,
            [a for a in acts if a["_date"] <= ds],
            [m for m in members if m.get("first_seen", "")[:10] <= ds or m["athlete_id"] in ran],
            label, roll)}
    return result


def load(cfg: config.Config) -> tuple:
    """-> (snapshots, ledger acts >= challenge_start, members, member_count, roll fitted over every name)."""
    acts = load_activities(cfg.challenge_start, ZoneInfo(cfg.timezone))
    snapshots = load_daily()
    members = load_members()
    # Strava stopped listing members, so members.csv misses joiners who have not run yet;
    # the headline count is the true total when we have it.
    member_count = max(load_member_count() or 0, len(members))
    roll = NominalRoll()
    # Fit once over every name: build_daily_history() re-resolves each athlete per day,
    # and a per-call match could land differently on different days.
    roll.fit({m.get("name", "") for m in members} | {a.get("athlete_name", "") for a in acts})
    print(f"Loaded {len(snapshots)} athlete-day snapshots, {len(acts)} ledger activities (>= {cfg.challenge_start}), "
          f"{len(members)} members.")
    return snapshots, acts, members, member_count, roll


def build(snapshots: list, acts: list, members: list, member_count: int, roll: NominalRoll, now_dt: datetime) -> tuple:
    """-> (today_data, daily_history); today and the latest snapshot carry the headline member_count."""
    extras = feed_updates(snapshots, acts)
    weeks = weeks_as_of(snapshots, extras, now_dt.date().isoformat())
    data = {"today": build_grouped_data(weeks, acts, members, day_label(now_dt), roll)}
    daily = build_daily_history(snapshots, extras, acts, members, roll, now_dt.date())
    data["today"]["all"]["athlete_count"] = member_count
    if daily:
        daily[max(daily)]["all"]["athlete_count"] = member_count
    return data, daily


def run() -> None:
    """load -> build -> render -> write index.html and the user-count badge."""
    cfg = config.load()
    now_dt = datetime.now(ZoneInfo(cfg.timezone))
    snapshots, acts, members, member_count, roll = load(cfg)
    data, daily = build(snapshots, acts, members, member_count, roll, now_dt)
    OUT_PATH.write_text(renderer.render(
        data, daily, f"{day_label(now_dt)} {now_dt.hour:02}:{now_dt.minute:02}",
        weather.weather_html(cfg.weather_lat, cfg.weather_lon, cfg.timezone),
        renderer.build_announcement_html(REPO_ROOT / cfg.announcement_path),
        cfg,
    ), encoding="utf-8")
    USER_COUNT_PATH.write_text(json.dumps({
        "schemaVersion": 1,
        "label": "users covered",
        "message": str(member_count),
        "color": "blue",
    }), encoding="utf-8")

    w = data["today"]["all"]
    print(f"Generated: {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.2f} MB)")
    print(f"  Cumulative: {w.get('run_count', 0)} activities, "
          f"{w.get('athlete_count', 0)} members, {w.get('total_km', 0):.0f} km")


if __name__ == "__main__":
    run()
