"""Unit tests for backfill's pure helpers (no browser, no network).

months_since() lists the Strava monthly interval ids to request; challenge_rows() keeps
only this athlete's public foot activities that started locally on/after the challenge.
"""
from datetime import date
from zoneinfo import ZoneInfo

from src.activities import backfill as B

SGT = ZoneInfo("Asia/Singapore")
START = "2026-09-14"


def _entry(activity_id, athlete_id="7", start="2026-09-20T00:00:00Z", type_="Run", visibility="everyone"):
    """A minimal "Activity" feed entry, the schema the profile interval XHR returns."""
    return {"entity": "Activity", "activity": {
        "id": activity_id, "startDate": start, "type": type_, "visibility": visibility,
        "athlete": {"athleteId": athlete_id, "athleteName": "A B", "firstName": "A"},
        "stats": [{"key": "stat_one", "value": "5.00 km"}, {"key": "stat_one_subtitle", "value": "Distance"}],
    }}


def _ids(rows):
    return [r["activity_id"] for r in rows]


def test_months_since_single_month():
    assert B.months_since(START, date(2026, 9, 27)) == ["202609"]


def test_months_since_crosses_year_end():
    assert B.months_since(START, date(2027, 1, 3)) == ["202609", "202610", "202611", "202612", "202701"]


def test_foot_activity_is_kept_and_parsed():
    rows = B.challenge_rows([_entry(1)], "7", START, SGT)
    assert _ids(rows) == ["1"]
    assert rows[0]["distance_m"] == 5000.0


def test_non_foot_types_are_skipped():
    entries = [_entry(1, type_="WeightTraining"), _entry(2, type_="Ride"), _entry(3, type_="Walk")]
    assert _ids(B.challenge_rows(entries, "7", START, SGT)) == ["3"]


def test_other_athletes_are_skipped():
    assert B.challenge_rows([_entry(1, athlete_id="8")], "7", START, SGT) == []


def test_start_filter_uses_local_date():
    # 2026-09-13T16:30Z is 00:30 on the 14th in Singapore -> counts; 15:30Z is the 13th -> doesn't.
    entries = [_entry(1, start="2026-09-13T16:30:00Z"), _entry(2, start="2026-09-13T15:30:00Z")]
    assert _ids(B.challenge_rows(entries, "7", START, SGT)) == ["1"]


def test_non_activity_entries_are_ignored():
    assert B.challenge_rows([{"entity": "Club"}, {"entity": "Challenge"}], "7", START, SGT) == []


def test_non_public_activities_are_skipped():
    entries = [_entry(1, visibility="followers_only"), _entry(2, visibility="only_me"), _entry(3)]
    assert _ids(B.challenge_rows(entries, "7", START, SGT)) == ["3"]
