"""Unit tests for validate.diff()'s reconciliation logic (no browser, no network).

Rules: an activity_id on Strava but not in the CSV is "missing_from_csv"; one in the
CSV but not on Strava is "missing_from_feed" only if its start_date_utc falls inside
the feed's own covered window (older rows are outside what the feed can attest to);
field differences between matched rows are reported as mismatches.
"""
from src.activities import validate as V


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

    result = V.diff(feed, csv_rows)

    assert result["missing_from_csv"] == []
    assert result["missing_from_feed"] == []
    assert result["mismatches"] == []


def test_activity_on_feed_but_not_in_csv_is_missing_from_csv():
    feed = [_feed_row("1")]

    result = V.diff(feed, {})

    assert result["missing_from_csv"] == ["1"]


def test_csv_row_inside_feed_window_but_absent_from_feed_is_flagged():
    feed = [_feed_row("1", start_date_utc="2026-09-14T00:00:00Z")]
    csv_rows = {
        "1": _csv_row("1", start_date_utc="2026-09-14T00:00:00Z"),
        "2": _csv_row("2", start_date_utc="2026-09-14T05:00:00Z"),  # inside window, absent
    }

    result = V.diff(feed, csv_rows)

    assert result["missing_from_feed"] == ["2"]


def test_csv_row_older_than_feed_window_is_not_flagged():
    feed = [_feed_row("1", start_date_utc="2026-09-14T00:00:00Z")]
    csv_rows = {
        "1": _csv_row("1", start_date_utc="2026-09-14T00:00:00Z"),
        "old": _csv_row("old", start_date_utc="2026-09-10T00:00:00Z"),  # before window
    }

    result = V.diff(feed, csv_rows)

    assert result["missing_from_feed"] == []


def test_field_mismatch_between_feed_and_csv_is_reported():
    feed = [_feed_row("1", activity_name="Afternoon Run")]
    csv_rows = {"1": _csv_row("1", activity_name="Morning Run")}

    result = V.diff(feed, csv_rows)

    assert result["mismatches"] == [("1", "activity_name", "Morning Run", "Afternoon Run")]


def test_report_summarises_each_section():
    result = V.diff([_feed_row("1")], {})

    text = V.report(result)

    assert "missing from activities.csv (1)" in text.lower()
    assert "1" in text
