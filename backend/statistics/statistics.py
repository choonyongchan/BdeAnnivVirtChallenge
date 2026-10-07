"""Statistics: each member's cumulative foot-sport totals since challenge_start, one statistics.csv row per athlete
per day, stamped synced_at. Every run overwrites the last seen figures, from the best source available:
    leaderboard  the club leaderboard (top 100, this and last week), hourly and nightly
    profile      the week's foot activities on the athlete's profile (MemberActivities), nightly
    feed         fallback for everyone else: their last row plus the ledger's foot activities it doesn't cover yet
A week's figures go on top of the athlete's last row before that week, so past weeks are never re-fetched.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from shared.config import settings
from shared.data import ACTIVITIES_CSV, FOOT_TYPES, STATISTICS_CSV, local_date, read_csv, write_csv
from ..activities.member_activities import monday_of, to_int, to_meters, to_seconds
from ..strava_session import CLUB_URL, ScrapeError, club_page

CSV_PATH = STATISTICS_CSV
FIGURES = ["distance_m", "moving_time_s", "elev_gain_m", "activities"]
FIELDS = ["athlete_id", "date", *FIGURES, "source", "synced_at"]

# Rank-table rows: the athlete's id comes from the profile link, the rest are cell texts.
ROWS_JS = """() => [...document.querySelectorAll('.leaderboard tbody tr')].map(tr => {
    const c = [...tr.children].map(td => td.innerText.trim());
    const a = tr.querySelector('a.athlete-name');
    return a && {id: a.getAttribute('href').split('/').pop(), name: a.innerText.trim(),
                 dist: c[2], acts: c[3], elev: c[4], time: c[5]};
}).filter(Boolean)"""


def parse_leaderboard(rows: list) -> dict:
    """Scraped rank-table rows -> {athlete_id: figures + name}."""
    return {r["id"]: {"name": r["name"],
                      "distance_m": to_meters(r["dist"]) or 0.0,
                      "moving_time_s": to_seconds(r["time"]) or 0,
                      "elev_gain_m": to_meters(r["elev"]) or 0.0,
                      "activities": to_int(r["acts"]) or 0}
            for r in rows}


def fetch_leaderboard() -> dict:
    """{monday_iso: {athlete_id: figures}} for this week and last week; the Last Week tab swaps the table in place."""
    this = monday_of(datetime.now(ZoneInfo(settings.timezone)).date())
    with club_page(f"{CLUB_URL}/leaderboard") as page:
        this_week = page.evaluate(ROWS_JS)
        page.locator("span.button.last-week").click()
        page.wait_for_timeout(1500)
        last_week = page.evaluate(ROWS_JS)
    if not this_week and not last_week:
        raise ScrapeError("Leaderboard empty - session expired or markup changed; re-run: python -m backend.login")
    return {this.isoformat(): parse_leaderboard(this_week),
            (this - timedelta(weeks=1)).isoformat(): parse_leaderboard(last_week)}


def snapshot_date(monday: str, today: date) -> str:
    """The day a week's snapshot stands for: its Sunday once over, else today."""
    return min(date.fromisoformat(monday) + timedelta(days=6), today).isoformat()


def week_totals(rows: list) -> dict:
    """Sum activity rows into FIGURES."""
    return {"distance_m": round(sum(float(r["distance_m"] or 0) for r in rows), 1),
            "moving_time_s": sum(int(float(r["moving_time_s"] or 0)) for r in rows),
            "elev_gain_m": round(sum(float(r["elev_gain_m"] or 0) for r in rows), 1),
            "activities": len(rows)}


def _add(base: dict, figs: dict) -> dict:
    """FIGURES of base + figs, rounded the way the CSV stores them."""
    out = {}
    for k in FIGURES:
        total = float(base.get(k) or 0) + float(figs[k])
        out[k] = round(total, 1) if k.endswith("_m") else int(total)
    return out


def _by_athlete(daily: dict) -> dict:
    by = defaultdict(dict)
    for (aid, day), r in daily.items():
        by[aid][day] = r
    return by


def cumulate(daily: dict, weeks: dict, leaderboard: dict, synced_at: str, today: date) -> dict:
    """Upsert {(athlete_id, date): row} with each week's figures added to the athlete's last row before that week.

    Args:
        daily: Existing statistics.csv rows keyed by (athlete_id, date).
        weeks: {(athlete_id, monday): foot activity rows} from MemberActivities.
        leaderboard: {monday: {athlete_id: figures}}; wins over the profile sum.
        synced_at: Stamp for every row written.
        today: Local date, caps the running week's snapshot date.
    """
    figs = {k: {**week_totals(rows), "source": "profile"} for k, rows in weeks.items()}
    for monday, board in leaderboard.items():
        for aid, fig in board.items():
            figs[(aid, monday)] = {**{k: fig[k] for k in FIGURES}, "source": "leaderboard"}

    by_athlete = _by_athlete(daily)
    for aid, monday in sorted(figs, key=lambda k: k[1]):   # oldest week first: Monday's last week is this week's base
        f = figs[(aid, monday)]
        if not (float(f["activities"]) or float(f["distance_m"])):
            continue   # nothing seen this week: no row, so feed-only runs (private profiles) keep counting
        mine = by_athlete[aid]
        prior = [d for d in mine if d < monday]
        base = mine[max(prior)] if prior else {}
        day = snapshot_date(monday, today)
        daily[(aid, day)] = mine[day] = {"athlete_id": aid, "date": day, **_add(base, f),
                                         "source": f["source"], "synced_at": synced_at}
    return daily


def fallback(daily: dict, acts: list, synced_at: str, today: date) -> dict:
    """Athletes the leaderboard and profiles didn't just cover: last row + ledger foot activities it doesn't include,
    i.e. dated after that row, or scraped after it was synced (late uploads). Upserted as today's "feed" row."""
    tz = ZoneInfo(settings.timezone)
    by_athlete = _by_athlete(daily)
    pending = defaultdict(list)
    for a in acts:
        d = local_date(a.get("start_date_utc"), tz)
        if a.get("type") not in FOOT_TYPES or not d or d < settings.challenge_start:
            continue
        mine = by_athlete.get(a["athlete_id"], {})
        last = mine[max(mine)] if mine else {"date": "", "synced_at": ""}
        if d > last["date"] or (a.get("scraped_at") or "") > (last["synced_at"] or ""):
            pending[a["athlete_id"]].append(a)
    day = today.isoformat()
    for aid, new in pending.items():
        mine = by_athlete[aid]
        base = mine[max(mine)] if mine else {}
        daily[(aid, day)] = {"athlete_id": aid, "date": day, **_add(base, week_totals(new)),
                             "source": "feed", "synced_at": synced_at}
    return daily


def run(weeks: dict, leaderboard: dict, synced_at: str, today: date) -> None:
    """Cumulate a run's sources, then the ledger fallback, into statistics.csv, rewritten sorted by (date, athlete_id)."""
    daily = cumulate({(r["athlete_id"], r["date"]): r for r in read_csv(CSV_PATH)},
                     weeks, leaderboard, synced_at, today)
    daily = fallback(daily, read_csv(ACTIVITIES_CSV), synced_at, today)
    write_csv(CSV_PATH, FIELDS, sorted(daily.values(), key=lambda r: (r["date"], r["athlete_id"])))
    print(f"{len(weeks)} athlete-weeks, {sum(map(len, leaderboard.values()))} leaderboard rows -> {CSV_PATH}")


def last_profile_sync() -> str:
    """When the last nightly profile scan synced (max synced_at of its rows), "" if never."""
    return max((r["synced_at"] for r in read_csv(CSV_PATH) if r["source"] == "profile"), default="")
