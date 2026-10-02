"""Unit tests for the activity-feed parsers: strip markup, key stats by label, coerce numbers (or None),
and fan a GroupActivity out to one row per member; plus the daily-snapshot helpers."""
from datetime import date

import pytest

from src.activities.activities import (
    FIELDS,
    _row,
    _text,
    foot_rows,
    merge_weeks,
    normalise,
    parse_leaderboard,
    parse_stats,
    to_int,
    to_meters,
    to_seconds,
    week_id,
    snapshot_date,
    weeks_to_sync,
)


@pytest.mark.parametrize("raw,text", [
    ("5.2 <abbr>km</abbr>", "5.2 km"),
    ("<p>line one<br />\nline two</p>", "line one\nline two"),
    (None, ""),
    (123, "123"),
])
def test_text_strips_markup(raw, text):
    assert _text(raw) == text


def test_parse_stats_keys_by_label_not_position():
    stats = [
        {"key": "stat_one", "value": "5.2 <abbr>km</abbr>"},
        {"key": "stat_one_subtitle", "value": "Distance"},
        {"key": "stat_two", "value": "25:30"},
        {"key": "stat_two_subtitle", "value": "Time"},
    ]
    assert parse_stats(stats) == {"Distance": "5.2 km", "Time": "25:30"}


@pytest.mark.parametrize("stats", [None, [], [{"key": "stat_one", "value": "9"}]])
def test_parse_stats_drops_unlabelled(stats):
    assert parse_stats(stats) == {}


@pytest.mark.parametrize("raw,metres", [
    ("5.2 km", 5200.0),
    ("3 mi", 4828.0),
    ("400 m", 400.0),
    ("1,234 km", 1234000.0),
    ("no distance", None),
    ("", None),
    (None, None),
])
def test_to_meters(raw, metres):
    assert to_meters(raw) == metres


@pytest.mark.parametrize("raw,seconds", [
    ("1h 5m 3s", 3903),
    ("45m", 2700),
    ("2h", 7200),
    ("nope", None),
    (None, None),
])
def test_to_seconds(raw, seconds):
    assert to_seconds(raw) == seconds


@pytest.mark.parametrize("raw,value", [
    ("12,345 steps", 12345),
    ("1 200", 1200),
    ("", None),
    (None, None),
])
def test_to_int(raw, value):
    assert to_int(raw) == value


def test_row_always_has_every_field_and_keeps_elapsed():
    row = _row(activity_id="a1", elapsed_time_s=1000,
               stats={"Distance": "5 km", "Time": "20m"})
    assert set(row) == set(FIELDS)
    assert row["activity_id"] == "a1"
    assert row["distance_m"] == 5000.0
    assert row["moving_time_s"] == 1200          # from stats["Time"]
    assert row["elapsed_time_s"] == 1000         # passed straight through
    assert row["elev_gain_m"] is None and row["steps"] is None


def test_normalise_activity_schema():
    entry = {
        "entity": "Activity",
        "activity": {
            "id": 111,
            "athlete": {"athleteId": 9, "athleteName": "Sam Q", "firstName": "Sam"},
            "startDate": "2026-09-14T07:00:00Z", "activityName": "Morning Run", "type": "Run",
            "elapsedTime": 2000, "deviceName": "Garmin",
            "stats": [
                {"key": "stat_one", "value": "5 <abbr>km</abbr>"},
                {"key": "stat_one_subtitle", "value": "Distance"},
                {"key": "stat_two", "value": "25m"},
                {"key": "stat_two_subtitle", "value": "Time"},
            ],
            "kudosAndComments": {"kudosCount": 4, "comments": [{}, {}]},
        },
    }
    (row,) = normalise(entry)
    assert row["entity"] == "Activity"
    assert row["activity_id"] == 111 and row["athlete_id"] == 9
    assert row["athlete_name"] == "Sam Q" and row["athlete_firstname"] == "Sam"
    assert row["start_date_utc"] == "2026-09-14T07:00:00Z"
    assert row["distance_m"] == 5000.0 and row["moving_time_s"] == 1500
    assert row["elapsed_time_s"] == 2000
    assert row["kudos_count"] == 4 and row["comment_count"] == 2


def test_normalise_group_activity_fans_out():
    entry = {
        "entity": "GroupActivity",
        "rowData": {"activities": [
            {"activity_id": 1, "athlete_id": 10, "athlete_name": "A",
             "start_date": "2026-09-14T01:00:00Z", "name": "Run A",
             "elapsed_time": 100, "num_comments": 3, "stats": []},
            {"activity_id": 2, "athlete_id": 11, "athlete_name": "B",
             "start_date": "2026-09-14T02:00:00Z", "name": "Run B",
             "elapsed_time": 200, "num_comments": 0, "stats": []},
        ]},
    }
    rows = normalise(entry)
    assert [r["activity_id"] for r in rows] == [1, 2]
    assert [r["comment_count"] for r in rows] == [3, 0]
    assert all(r["entity"] == "GroupActivity" for r in rows)


@pytest.mark.parametrize("entry,expected_len", [
    ({"entity": "Club"}, 0),
    ({}, 0),
    ({"entity": "GroupActivity"}, 0),          # no rowData -> nothing
])
def test_normalise_unknown_or_empty(entry, expected_len):
    assert len(normalise(entry)) == expected_len


def test_normalise_activity_without_body_is_safe():
    (row,) = normalise({"entity": "Activity"})
    assert row["activity_id"] is None          # caller drops it later


# --- weekly sync helpers ------------------------------------------------------

@pytest.mark.parametrize("monday,wid", [(date(2026, 9, 14), "202638"), (date(2026, 12, 28), "202653"),
                                        (date(2027, 1, 4), "202701")])
def test_week_id_is_iso_year_and_week(monday, wid):
    assert week_id(monday) == wid


@pytest.mark.parametrize("today,setup,mondays", [
    (date(2026, 10, 1), False, ["2026-09-28"]),                              # Thursday: this week
    (date(2026, 10, 5), False, ["2026-09-28", "2026-10-05"]),                # Monday: plus last week
    (date(2026, 10, 4), False, ["2026-09-28"]),                              # Sunday still this week
    (date(2026, 10, 1), True, ["2026-09-14", "2026-09-21", "2026-09-28"]),   # setup: since the start
])
def test_weeks_to_sync(today, setup, mondays):
    assert [m.isoformat() for m in weeks_to_sync(today, "2026-09-14", setup)] == mondays


def test_parse_leaderboard_row():
    rows = [{"id": "27799486", "name": "Joseph Soh", "dist": "94.6 km", "acts": "10", "elev": "1,254 m",
             "time": "8h 34m"}]
    assert parse_leaderboard(rows) == {"27799486": {
        "name": "Joseph Soh", "distance_m": 94600.0, "moving_time_s": 30840, "elev_gain_m": 1254.0, "activities": 10}}


def test_foot_rows_keeps_only_own_foot_activities():
    def entry(aid, athlete, type_):
        return {"entity": "Activity", "activity": {"id": aid, "athlete": {"athleteId": athlete}, "type": type_}}
    rows = foot_rows([entry(1, 7, "Run"), entry(2, 7, "Walk"), entry(3, 7, "Ride"), entry(4, 8, "Run")], "7")
    assert [r["activity_id"] for r in rows] == ["1", "2"]


@pytest.mark.parametrize("monday,today,day", [
    ("2026-09-28", date(2026, 10, 1), "2026-10-01"),   # running week: today
    ("2026-09-21", date(2026, 9, 28), "2026-09-27"),   # last week, re-synced on Monday: its Sunday
])
def test_snapshot_date(monday, today, day):
    assert snapshot_date(monday, today) == day


def test_merge_weeks_leaderboard_beats_profile_and_earlier_days_stay():
    old = {("1", "2026-09-27"): {"athlete_id": "1", "date": "2026-09-27", "week": "2026-09-21", "distance_m": "9000"},
           ("1", "2026-09-30"): {"athlete_id": "1", "date": "2026-09-30", "week": "2026-09-28", "distance_m": "3000"}}
    profile = [{"athlete_id": "1", "date": "2026-10-01", "week": "2026-09-28", "distance_m": 5000,
                "moving_time_s": 1800, "elev_gain_m": 0, "activities": 1, "source": "profile"}]
    board = {"2026-09-28": {"1": {"name": "A", "distance_m": 7000.0, "moving_time_s": 2400,
                                  "elev_gain_m": 5.0, "activities": 2}}}
    merged = merge_weeks(old, profile, board, "T", date(2026, 10, 1))
    assert merged[("1", "2026-09-27")]["distance_m"] == "9000"
    assert merged[("1", "2026-09-30")]["distance_m"] == "3000"
    assert merged[("1", "2026-10-01")] == {"athlete_id": "1", "date": "2026-10-01", "week": "2026-09-28",
                                           "source": "leaderboard", "synced_at": "T",
                                           "distance_m": 7000.0, "moving_time_s": 2400, "elev_gain_m": 5.0,
                                           "activities": 2}
