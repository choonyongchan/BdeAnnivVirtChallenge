"""Unit tests for the activity-feed parsers.

Strava's feed carries stat values wrapped in markup, labelled out of order, and
in two entirely different entry schemas ("Activity" camelCase vs
"GroupActivity" snake_case). These parsers must strip the markup, key stats by
their label not their position, coerce distances/durations to numbers (or
None), and fan a GroupActivity out to one row per member.
"""
import pytest

from src.activities.activities import (
    FIELDS,
    _row,
    _text,
    normalise,
    parse_stats,
    to_int,
    to_meters,
    to_seconds,
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
