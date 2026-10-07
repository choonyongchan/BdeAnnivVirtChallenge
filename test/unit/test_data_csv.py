"""Unit tests for shared/data.py's CSV helpers."""
from shared.data import append_new_rows


def test_append_new_rows_writes_header_once_across_calls(tmp_path):
    path = tmp_path / "activities.csv"
    append_new_rows(path, ["activity_id"], [{"activity_id": "1"}])
    append_new_rows(path, ["activity_id"], [{"activity_id": "2"}])

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == ["activity_id", "1", "2"]
