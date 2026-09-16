"""Unit tests for validate.diff()'s reconciliation logic (no browser, no network).

Rules: an activity_id on Strava but not in the CSV is "missing_from_csv"; one in the
CSV but not on Strava is "missing_from_feed" only if it started after the feed's floor
(older rows are outside what the feed can attest to); field differences between matched
rows are reported as mismatches.

The floor comes from feed_floor() - cursorData.updated_at, the key the feed paginates
by - not from start_date, which is a different ordering and flags live activities.
"""
import pytest

from src.activities import validate as V
from src.strava_session import ScrapeError

WINDOW = "2026-09-14T00:00:00Z"  # feed_floor() output; diff() takes it explicitly


def _feed_row(activity_id="1", start_date_utc="2026-09-14T00:00:00Z", **extra):
    row = dict.fromkeys(V.COMPARE_FIELDS)
    row["activity_id"] = activity_id
    row["start_date_utc"] = start_date_utc
    row.update(extra)
    return row


def _csv_row(activity_id="1", start_date_utc="2026-09-14T00:00:00Z", **extra):
    row = {f: "" for f in V.COMPARE_FIELDS}
    row["activity_id"] = activity_id
    row["start_date_utc"] = start_date_utc
    row.update(extra)
    return row


def test_identical_rows_produce_no_findings():
    feed = [_feed_row("1", activity_name="Run", distance_m=1000.0)]
    csv_rows = {"1": _csv_row("1", activity_name="Run", distance_m="1000.0")}

    result = V.diff(feed, csv_rows, WINDOW)

    assert result["missing_from_csv"] == []
    assert result["missing_from_feed"] == []
    assert result["mismatches"] == []


def test_activity_on_feed_but_not_in_csv_is_missing_from_csv():
    feed = [_feed_row("1")]

    result = V.diff(feed, {}, WINDOW)

    assert result["missing_from_csv"] == ["1"]


def test_csv_row_inside_feed_window_but_absent_from_feed_is_flagged():
    feed = [_feed_row("1", start_date_utc="2026-09-14T00:00:00Z")]
    csv_rows = {
        "1": _csv_row("1", start_date_utc="2026-09-14T00:00:00Z"),
        "2": _csv_row("2", start_date_utc="2026-09-14T05:00:00Z"),  # inside window, absent
    }

    result = V.diff(feed, csv_rows, WINDOW)

    assert result["missing_from_feed"] == ["2"]


def test_csv_row_older_than_feed_window_is_not_flagged():
    feed = [_feed_row("1", start_date_utc="2026-09-14T00:00:00Z")]
    csv_rows = {
        "1": _csv_row("1", start_date_utc="2026-09-14T00:00:00Z"),
        "old": _csv_row("old", start_date_utc="2026-09-10T00:00:00Z"),  # before window
    }

    result = V.diff(feed, csv_rows, WINDOW)

    assert result["missing_from_feed"] == []


def test_field_mismatch_between_feed_and_csv_is_reported():
    feed = [_feed_row("1", activity_name="Afternoon Run")]
    csv_rows = {"1": _csv_row("1", activity_name="Morning Run")}

    result = V.diff(feed, csv_rows, WINDOW)

    assert result["mismatches"] == [("1", "activity_name", "Morning Run", "Afternoon Run")]


def test_report_summarises_each_section():
    result = V.diff([_feed_row("1")], {}, WINDOW)

    text = V.report(result)

    assert "missing from activities.csv (1)" in text.lower()
    assert "1" in text


def test_feed_floor_is_oldest_updated_at_as_iso():
    entries = [
        {"cursorData": {"updated_at": 1789571080, "rank": 1}},
        {"cursorData": {"updated_at": 1789518526, "rank": 2}},  # oldest
    ]

    assert V.feed_floor(entries) == "2026-09-16T00:28:46Z"


def test_feed_floor_raises_when_entries_carry_no_cursor():
    with pytest.raises(ScrapeError):
        V.feed_floor([{"entity": "Activity"}])


def test_csv_row_started_before_the_updated_at_floor_is_not_flagged():
    """Regression: the feed is ordered by update time, so it can carry an activity
    that started at 23:39 while an activity that started at 00:06 has already rolled
    off. Windowing on the feed's min start_date flagged these as missing though they
    were still live on Strava."""
    feed = [_feed_row("1", start_date_utc="2026-09-15T23:39:23Z")]
    csv_rows = {
        "1": _csv_row("1", start_date_utc="2026-09-15T23:39:23Z"),
        "2": _csv_row("2", start_date_utc="2026-09-16T00:06:10Z"),  # below the floor
    }

    result = V.diff(feed, csv_rows, "2026-09-16T00:28:46Z")

    assert result["missing_from_feed"] == []
