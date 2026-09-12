"""Unit tests for strava_session.py's two CSV helpers, in isolation from any scraper.

Uses test/fixtures/activities_sample.csv (anonymised real data) as the well-formed
CSV input; the missing-field case has no real-world analogue, so it stays synthetic.
"""
import shutil
from pathlib import Path

import pytest

from src.strava_session import append_new_rows, csv_column_set

FIXTURE = Path(__file__).parent.parent / "fixtures" / "activities_sample.csv"


def test_csv_column_set_missing_file_returns_empty_set(tmp_path):
    assert csv_column_set(tmp_path / "none.csv", "activity_id") == set()


def test_csv_column_set_returns_values_of_field(tmp_path):
    path = tmp_path / "activities.csv"
    shutil.copy(FIXTURE, path)
    assert csv_column_set(path, "activity_id") == {
        "1000000001", "1000000002", "1000000003", "1000000004", "1000000005",
    }


def test_csv_column_set_missing_field_raises_keyerror(tmp_path):
    path = tmp_path / "activities.csv"
    path.write_text("activity_id,athlete_id\n1,7\n", encoding="utf-8")
    with pytest.raises(KeyError):
        csv_column_set(path, "not_a_column")


def test_append_new_rows_writes_header_once_across_calls(tmp_path):
    path = tmp_path / "activities.csv"
    append_new_rows(path, ["activity_id"], [{"activity_id": "1"}])
    append_new_rows(path, ["activity_id"], [{"activity_id": "2"}])

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == ["activity_id", "1", "2"]
