"""Integration test for the CSV writers (no browser): the ledger and members.csv are append-only and
deduped by id; existing member rows are never rewritten."""
import csv
import json

import pytest

from src.activities import member_activities as A
from src.activities import recent_activities as R
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

    A.append_activities(A.normalise(_activity(1, 7)))
    A.append_activities(A.normalise(_activity(1, 7)) + A.normalise(_activity(2, 8)))   # 1 repeats, 2 is new

    rows = _read(path)
    assert [r["activity_id"] for r in rows] == ["1", "2"]           # no duplicate row for 1
    assert all(r["scraped_at"] for r in rows)
    assert path.read_text(encoding="utf-8").count("activity_id,athlete_id") == 1  # header once


@pytest.fixture
def member_paths(tmp_path, monkeypatch):
    paths = {"csv": tmp_path / "members.csv", "count": tmp_path / "member_count.json"}
    monkeypatch.setattr(M, "CSV_PATH", paths["csv"])
    monkeypatch.setattr(M, "COUNT_PATH", paths["count"])
    return paths


def test_members_write_is_append_only(member_paths):
    assert M.write_members(1000, {"1": "Alice Anon"}) == 1
    assert M.write_members(1001, {"1": "Alice A.", "3": "Cara Cipher"}) == 1   # Alice renamed, Cara new
    assert M.write_members(1001, {"3": "Cara Cipher"}) == 0                      # nothing new on a rerun

    rows = _read(member_paths["csv"])
    assert [(r["athlete_id"], r["name"]) for r in rows] == [("1", "Alice Anon"), ("3", "Cara Cipher")]
    assert all(r["first_seen"] for r in rows)
    assert member_paths["csv"].read_text(encoding="utf-8").count("athlete_id,name,first_seen") == 1
    assert json.loads(member_paths["count"].read_text(encoding="utf-8")) == {"member_count": 1001}


def test_recent_activities_adds_feed_and_leaderboard_athletes_and_ledgers_feed_runs(member_paths, monkeypatch,
                                                                               tmp_path):
    monkeypatch.setattr(A, "CSV_PATH", tmp_path / "activities.csv")
    monkeypatch.setattr(R, "require_auth", lambda: None)
    feed = A.normalise(_activity(1, 1)) + A.normalise(_activity(2, 1))
    feed[0].update(type="Run", athlete_name="Alice Anon")
    feed[1].update(type="Ride", athlete_name="Alice Anon")   # not a foot activity: kept out of the ledger
    monkeypatch.setattr(R, "fetch_count_and_feed", lambda: (1055, feed))
    board = {"2026-09-28": {"2": {"name": "Bob Bogus"}}, "2026-09-21": {"1": {"name": "Alice Anon"}}}

    assert R.run(board) == 2
    assert {r["athlete_id"] for r in _read(member_paths["csv"])} == {"1", "2"}
    assert [r["activity_id"] for r in _read(tmp_path / "activities.csv")] == ["1"]
