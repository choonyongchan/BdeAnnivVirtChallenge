"""MemberStatistics: each member's cumulative totals since challenge_start, one daily.csv row per athlete per day.
Each scanned week's totals (the club leaderboard wins over the profile sum) go on top of the athlete's last row
before that week, so nightly runs never re-fetch past weeks. Stamped synced_at; the dashboard adds ledger
activities scraped after it (the hourly club feed) on top."""
import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from ..strava_session import read_csv

DAILY_CSV = Path(__file__).parent / "daily.csv"
FIGURES = ["distance_m", "moving_time_s", "elev_gain_m", "activities"]
DAILY_FIELDS = ["athlete_id", "date", *FIGURES, "source", "synced_at"]


def snapshot_date(monday: str, today: date) -> str:
    """The day a week's snapshot stands for: its Sunday once over, else today."""
    return min(date.fromisoformat(monday) + timedelta(days=6), today).isoformat()


def week_totals(rows: list) -> dict:
    """Sum a profile week's foot activities into FIGURES."""
    return {"distance_m": round(sum(r["distance_m"] or 0 for r in rows), 1),
            "moving_time_s": sum(r["moving_time_s"] or 0 for r in rows),
            "elev_gain_m": round(sum(r["elev_gain_m"] or 0 for r in rows), 1),
            "activities": len(rows)}


def cumulate(daily: dict, weeks: dict, leaderboard: dict, synced_at: str, today: date) -> dict:
    """Upsert {(athlete_id, date): row} with each week's figures added to the athlete's last row before that week.

    Args:
        daily: Existing daily.csv rows keyed by (athlete_id, date).
        weeks: {(athlete_id, monday): foot activity rows} from MemberActivities.
        leaderboard: {monday: {athlete_id: figures}}; wins over the profile sum.
        synced_at: Stamp for every row written.
        today: Local date, caps the running week's snapshot date.
    """
    figs = {k: {**week_totals(rows), "source": "profile"} for k, rows in weeks.items()}
    for monday, board in leaderboard.items():
        for aid, fig in board.items():
            figs[(aid, monday)] = {**{k: fig[k] for k in FIGURES}, "source": "leaderboard"}

    by_athlete = defaultdict(dict)
    for (aid, day), r in daily.items():
        by_athlete[aid][day] = r
    for aid, monday in sorted(figs, key=lambda k: k[1]):   # oldest week first: Monday's last week is this week's base
        f = figs[(aid, monday)]
        if not (float(f["activities"]) or float(f["distance_m"])):
            continue   # nothing seen this week: no row, so feed-only runs (private profiles) keep counting
        mine = by_athlete[aid]
        prior = [d for d in mine if d < monday]
        base = mine[max(prior)] if prior else {}
        day = snapshot_date(monday, today)
        row = {"athlete_id": aid, "date": day, "source": f["source"], "synced_at": synced_at}
        for k in FIGURES:
            total = float(base.get(k) or 0) + float(f[k])
            row[k] = round(total, 1) if k.endswith("_m") else int(total)
        daily[(aid, day)] = mine[day] = row
    return daily


def run(weeks: dict, leaderboard: dict, synced_at: str, today: date) -> None:
    """Cumulate a scan's weeks into daily.csv, rewritten sorted by (date, athlete_id)."""
    daily = cumulate({(r["athlete_id"], r["date"]): r for r in read_csv(DAILY_CSV)},
                     weeks, leaderboard, synced_at, today)
    with DAILY_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DAILY_FIELDS)
        w.writeheader()
        w.writerows(sorted(daily.values(), key=lambda r: (r["date"], r["athlete_id"])))
    print(f"{len(weeks)} athlete-weeks -> {DAILY_CSV}")
