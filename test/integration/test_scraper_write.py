"""Integration test for the scrapers' write() steps (no browser): both CSVs are append-only and deduped
by id; members get first_seen = first activity, existing rows are never rewritten."""
import csv
import json

import pytest

from src.activities import activities as A
from src.members import members as M


def _read(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _activity(aid, athlete_id, start="2026-09-14T07:00:00Z"):
    return {
        "entity": "Activity",
        "activity": {
            "id": aid, "athlete": {"athleteId": athlete_id},
            "startDate": start, "stats": [], "kudosAndComments": {},
        },
    }


def test_activities_write_is_append_only_and_deduped(tmp_path, monkeypatch):
    path = tmp_path / "activities.csv"
    monkeypatch.setattr(A, "CSV_PATH", path)

    A.RecentActivityFeed().write([_activity(1, 7)])
    A.RecentActivityFeed().write([_activity(1, 7), _activity(2, 8)])   # 1 repeats, 2 is new

    rows = _read(path)
    assert [r["activity_id"] for r in rows] == ["1", "2"]           # no duplicate row for 1
    assert all(r["scraped_at"] for r in rows)
    assert path.read_text(encoding="utf-8").count("activity_id,athlete_id") == 1  # header once


def test_activities_write_drops_idless_rows_and_expands_group(tmp_path, monkeypatch):
    path = tmp_path / "activities.csv"
    monkeypatch.setattr(A, "CSV_PATH", path)

    idless = {"entity": "Activity"}                                 # normalises to activity_id=None
    group = {"entity": "GroupActivity", "rowData": {"activities": [
        {"activity_id": 10, "athlete_id": 1, "start_date": "2026-09-14T01:00:00Z", "stats": []},
        {"activity_id": 11, "athlete_id": 2, "start_date": "2026-09-14T02:00:00Z", "stats": []},
    ]}}
    A.RecentActivityFeed().write([idless, group])

    assert [r["activity_id"] for r in _read(path)] == ["10", "11"]


def _write_activities(path, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["athlete_id", "athlete_name", "start_date_utc"])
        w.writerows(rows)


@pytest.fixture
def member_paths(tmp_path, monkeypatch):
    paths = {"csv": tmp_path / "members.csv", "acts": tmp_path / "activities.csv",
             "count": tmp_path / "member_count.json"}
    monkeypatch.setattr(M, "CSV_PATH", paths["csv"])
    monkeypatch.setattr(M, "ACTIVITIES_CSV", paths["acts"])
    monkeypatch.setattr(M, "COUNT_PATH", paths["count"])
    return paths


def test_members_write_adds_activity_athletes_at_first_run(member_paths):
    _write_activities(member_paths["acts"], [
        ["2", "Bob Bogus", "2026-09-18T07:00:00Z"],
        ["2", "Bob Bogus", "2026-09-17T06:00:00Z"],      # Bob's earliest run
        ["", "No Id", "2026-09-17T06:00:00Z"],           # id-less: skipped
    ])

    assert M.MemberScraper().write(1055) == 1

    assert _read(member_paths["csv"]) == [
        {"athlete_id": "2", "name": "Bob Bogus", "first_seen": "2026-09-17T06:00:00+00:00"}]
    assert json.loads(member_paths["count"].read_text(encoding="utf-8")) == {"member_count": 1055}


def test_members_write_is_append_only(member_paths):
    _write_activities(member_paths["acts"], [["1", "Alice Anon", "2026-09-15T00:00:00Z"]])
    M.MemberScraper().write(1000)

    _write_activities(member_paths["acts"], [               # Alice renamed, Cara new
        ["1", "Alice A.", "2026-09-14T00:00:00Z"],
        ["3", "Cara Cipher", "2026-09-20T00:00:00Z"],
    ])
    assert M.MemberScraper().write(1001) == 1
    assert M.MemberScraper().write(1001) == 0               # nothing new on a rerun

    by_id = {r["athlete_id"]: r for r in _read(member_paths["csv"])}
    assert by_id["1"] == {"athlete_id": "1", "name": "Alice Anon",   # row untouched
                          "first_seen": "2026-09-15T00:00:00+00:00"}
    assert by_id["3"]["first_seen"] == "2026-09-20T00:00:00+00:00"
    assert member_paths["csv"].read_text(encoding="utf-8").count("athlete_id,name,first_seen") == 1
    assert json.loads(member_paths["count"].read_text(encoding="utf-8")) == {"member_count": 1001}
