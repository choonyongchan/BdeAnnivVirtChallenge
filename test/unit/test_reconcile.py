"""Unit tests for the leaderboard reconciliation: week windows, leaderboard parsing, CSV totals and shortfall detection."""
import csv
from datetime import date
from zoneinfo import ZoneInfo

from src.activities import activities as A
from src.activities import reconcile as R

SGT = ZoneInfo("Asia/Singapore")


def test_week_ranges_are_monday_to_sunday():
    w = R.week_ranges(date(2026, 10, 1), "2026-09-14")  # a Thursday
    assert w == {"this": ("2026-09-28", "2026-10-04"), "last": ("2026-09-21", "2026-09-27")}


def test_week_ranges_clamp_to_challenge_start():
    w = R.week_ranges(date(2026, 9, 16), "2026-09-15")
    assert w["this"][0] == "2026-09-15" and w["last"] == ("2026-09-15", "2026-09-13")  # last week: empty window


def test_parse_leaderboard_reads_id_distance_and_count():
    rows = [{"id": "7", "dist": "58.1 km", "acts": "10"}, {"id": None, "dist": "1 km", "acts": "1"}]
    assert R.parse_leaderboard(rows) == {"7": (10, 58100.0)}


def test_shortfalls_flag_missing_activities_or_distance_only():
    board = {"1": (3, 10000.0), "2": (2, 5000.0), "3": (2, 5000.0), "4": (1, 5100.0)}
    mine = {"1": (3, 10000.0), "2": (1, 5000.0), "3": (3, 9000.0)}
    # 1 matches; 2 lacks an activity; 3 has extras (fine); 4 absent from the CSV entirely.
    assert set(R.shortfalls(board, mine)) == {"2", "4"}


def test_shortfalls_ignore_rounding_slack():
    assert R.shortfalls({"1": (1, 5050.0)}, {"1": (1, 5000.0)}) == {}
    assert set(R.shortfalls({"1": (1, 5500.0)}, {"1": (1, 5000.0)})) == {"1"}


def test_csv_totals_counts_foot_activities_in_local_week(tmp_path, monkeypatch):
    path = tmp_path / "activities.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=A.FIELDS)
        w.writeheader()
        for aid, start, type_, dist in [
            (1, "2026-09-27T15:30:00Z", "Run", 5000),   # 23:30 Sunday SGT -> last week
            (2, "2026-09-27T16:30:00Z", "Run", 4000),   # 00:30 Monday SGT -> this week
            (3, "2026-09-29T00:00:00Z", "Ride", 9000),  # not a foot activity
            (4, "2026-09-29T00:00:00Z", "Walk", 1000),
        ]:
            w.writerow({"activity_id": aid, "athlete_id": "7", "start_date_utc": start, "type": type_, "distance_m": dist})
    monkeypatch.setattr(R, "CSV_PATH", path)
    assert R.csv_totals(("2026-09-28", "2026-10-04"), SGT) == {"7": (2, 5000.0)}
    assert R.csv_totals(("2026-09-21", "2026-09-27"), SGT) == {"7": (1, 5000.0)}


def test_profile_scan_covers_every_month_from_challenge_start():
    # Challenge starts 14 Sep: September must be scanned, not just the months after it.
    assert A.months_since("2026-09-14", date(2026, 10, 1)) == ["202609", "202610"]
