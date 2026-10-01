"""Cross-check activities.csv against the club leaderboard's weekly totals (this week and last week,
top 100 ranks - all Strava exposes), then rescan only the members whose totals fall short.
    python -m src.activities.reconcile [--fix]
Without --fix it only reports. A shortfall that survives the rescan is an activity the scraping
account can't see (typically followers-only); the leaderboard counts it, no profile page lists it.
"""
import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import settings
from ..dashboard.generate import _local_date
from ..strava_session import CLUB_URL, club_page, read_csv, require_auth
from .activities import CSV_PATH, FOOT_TYPES, MEMBERS_CSV, ProfilesFeed, to_meters

DIST_SLACK_M = 200  # the leaderboard rounds distance to 0.1 km per athlete

# Rows of the rank table: the athlete's id comes from the profile link, the rest are cell texts.
ROWS_JS = """() => [...document.querySelectorAll('.leaderboard tbody tr')].map(tr => {
    const c = [...tr.children].map(td => td.innerText.trim());
    const a = tr.querySelector('a.athlete-name');
    return {id: a && a.getAttribute('href').split('/').pop(), dist: c[2], acts: c[3]};
})"""


def week_ranges(today: date, challenge_start: str) -> dict:
    """{'this': (first, last), 'last': (first, last)} ISO local dates, Monday-Sunday weeks,
    the first day clamped to the challenge start."""
    monday = today - timedelta(days=today.weekday())
    weeks = {"this": monday, "last": monday - timedelta(days=7)}
    return {k: (max(m.isoformat(), challenge_start), (m + timedelta(days=6)).isoformat())
            for k, m in weeks.items()}


def parse_leaderboard(rows: list) -> dict:
    """Scraped rank-table rows -> {athlete_id: (activities, metres)}."""
    return {r["id"]: (int(r["acts"] or 0), to_meters(r["dist"]) or 0.0) for r in rows if r["id"]}


def fetch_leaderboard() -> dict:
    """{'this': {...}, 'last': {...}} parsed leaderboards; the Last Week tab swaps the table in place."""
    with club_page(f"{CLUB_URL}/leaderboard") as page:
        this_week = page.evaluate(ROWS_JS)
        page.locator("span.button.last-week").click()
        page.wait_for_timeout(1500)
        last_week = page.evaluate(ROWS_JS)
    return {"this": parse_leaderboard(this_week), "last": parse_leaderboard(last_week)}


def csv_totals(week: tuple, tzinfo) -> dict:
    """{athlete_id: (activities, metres)} for foot activities in activities.csv starting locally within week."""
    totals = defaultdict(lambda: [0, 0.0])
    for r in read_csv(CSV_PATH):
        if r["type"] in FOOT_TYPES and week[0] <= _local_date(r["start_date_utc"], tzinfo) <= week[1]:
            totals[r["athlete_id"]][0] += 1
            totals[r["athlete_id"]][1] += float(r["distance_m"] or 0)
    return {a: tuple(t) for a, t in totals.items()}


def shortfalls(board: dict, mine: dict) -> dict:
    """{athlete_id: (board, mine)} where the leaderboard shows more activities or distance than the CSV."""
    out = {}
    for aid, (acts, metres) in board.items():
        n, m = mine.get(aid, (0, 0.0))
        if acts > n or metres > m + DIST_SLACK_M:
            out[aid] = ((acts, metres), (n, m))
    return out


def gaps(boards: dict, tzinfo) -> dict:
    """{week: {athlete_id: ((lb_acts, lb_m), (csv_acts, csv_m))}} for the weeks that fall short."""
    weeks = week_ranges(datetime.now(tzinfo).date(), settings.challenge_start)
    return {k: s for k, s in ((k, shortfalls(boards[k], csv_totals(weeks[k], tzinfo))) for k in weeks) if s}


def report(gap_by_week: dict, names: dict) -> None:
    for week, gap in gap_by_week.items():
        print(f"{week} week: {len(gap)} member(s) short")
        for aid, ((acts, metres), (n, m)) in sorted(gap.items(), key=lambda kv: -kv[1][0][1]):
            print(f"  {aid:>12} {names.get(aid, '?'):<30} leaderboard {acts:>3} / {metres / 1000:6.1f} km"
                  f"   csv {n:>3} / {m / 1000:6.1f} km")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fix", action="store_true", help="rescan the short members' profiles")
    args = parser.parse_args()

    require_auth()
    tzinfo = ZoneInfo(settings.timezone)
    names = {r["athlete_id"]: r["name"] for r in read_csv(MEMBERS_CSV)}
    boards = fetch_leaderboard()
    print(f"leaderboard rows: this week {len(boards['this'])}, last week {len(boards['last'])}")

    gap = gaps(boards, tzinfo)
    report(gap, names)
    ids = {a for g in gap.values() for a in g}
    if not ids:
        print("activities.csv matches the leaderboard.")
        return 0
    if not args.fix:
        return 1

    scannable = ids & names.keys()
    print(f"\nrescanning {len(scannable)} member profile(s); {len(ids - scannable)} not in members.csv")
    if scannable:
        ProfilesFeed(only=scannable).run()
    gap = gaps(boards, tzinfo)
    print("\nStill short after rescan (activities this account can't see, e.g. followers-only):")
    report(gap, names)
    return 0 if not gap else 1


if __name__ == "__main__":
    raise SystemExit(main())
