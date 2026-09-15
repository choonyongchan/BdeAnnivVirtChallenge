"""Read-only reconciliation of activities.csv against the live Strava club feed.

The feed only retains ~2.5 days (see activities.py's docstring), so this can only
confirm the CSV against that recent window - it is not a full-history audit. Rows
older than the feed's own oldest entry are outside what the feed can attest to and
are never reported as missing.

Requires an existing saved session (python -m src.login); never writes to
activities.csv.

    python -m src.activities.validate
"""
import csv
import sys

from ..strava_session import AUTH_PATH, ScrapeError
from .activities import CSV_PATH, ActivityScraper, normalise

COMPARE_FIELDS = [
    "athlete_name", "athlete_firstname", "activity_name", "type",
    "distance_m", "moving_time_s", "elapsed_time_s", "pace", "elev_gain_m", "steps",
    "device_name", "workout_type", "is_virtual", "is_commute",
    "visibility", "location", "description", "start_date_utc",
]


def _csv_rows() -> dict:
    """activities.csv -> {activity_id: row}."""
    if not CSV_PATH.exists():
        return {}
    with CSV_PATH.open(encoding="utf-8", newline="") as f:
        return {r["activity_id"]: r for r in csv.DictReader(f)}


def diff(feed_rows: list, csv_rows: dict) -> dict:
    """Compare normalised feed rows (activities.py's normalise() output) against
    activities.csv rows (activity_id -> row dict).

    "missing_from_feed" only lists csv rows whose start_date_utc falls within the
    feed's own covered window (its oldest entry's start_date_utc) - anything older
    is outside what this feed snapshot can attest to, and is not reported."""
    feed_by_id = {str(r["activity_id"]): r for r in feed_rows if r["activity_id"]}
    window_start = min(
        (r["start_date_utc"] for r in feed_by_id.values() if r["start_date_utc"]),
        default=None,
    )

    missing_from_csv = sorted(aid for aid in feed_by_id if aid not in csv_rows)
    missing_from_feed = sorted(
        aid for aid, row in csv_rows.items()
        if aid not in feed_by_id and window_start and (row.get("start_date_utc") or "") >= window_start
    )

    mismatches = []
    for aid, feed_row in feed_by_id.items():
        csv_row = csv_rows.get(aid)
        if not csv_row:
            continue
        for field in COMPARE_FIELDS:
            feed_val = "" if feed_row.get(field) is None else str(feed_row[field])
            csv_val = csv_row.get(field) or ""
            if feed_val != csv_val:
                mismatches.append((aid, field, csv_val, feed_val))

    return {
        "window_start": window_start,
        "missing_from_csv": missing_from_csv,
        "missing_from_feed": missing_from_feed,
        "mismatches": mismatches,
    }


def report(result: dict) -> str:
    """A plain-text summary of a diff() result."""
    lines = [
        f"Feed window covers activities from {result['window_start']} onward."
        if result["window_start"] else "Feed returned no entries."
    ]

    lines.append(f"\nOn Strava but missing from activities.csv ({len(result['missing_from_csv'])}):")
    lines += [f"  {aid}" for aid in result["missing_from_csv"]] or ["  (none)"]

    lines.append(f"\nIn activities.csv but not on Strava, within feed window ({len(result['missing_from_feed'])}):")
    lines += [f"  {aid}" for aid in result["missing_from_feed"]] or ["  (none)"]

    lines.append(f"\nField mismatches ({len(result['mismatches'])}):")
    lines += [
        f"  {aid}: {field} csv={csv_val!r} strava={feed_val!r}"
        for aid, field, csv_val, feed_val in result["mismatches"]
    ] or ["  (none)"]

    return "\n".join(lines)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # device names etc. may be non-ASCII

    if not AUTH_PATH.exists():
        raise ScrapeError("No saved session. Run: python -m src.login")

    entries = ActivityScraper().fetch()
    feed_rows = [r for e in entries for r in normalise(e) if r["activity_id"]]
    result = diff(feed_rows, _csv_rows())

    print(report(result))
    return 1 if (result["missing_from_csv"] or result["missing_from_feed"] or result["mismatches"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
