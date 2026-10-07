"""Integration test for the CSV writers (no browser): the ledger is append-only and deduped by id; members.csv
follows the roster (ingest_at, left_at, rejoins) only when it tallies with the headline; member_count.csv is a
history of headlines; the feed ledgers every sport."""
import csv
from contextlib import contextmanager

import pytest

from backend.activities import feed as F
from backend.activities import member_activities as A
from backend.members import members as M


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


def test_feed_ledgers_every_sport(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "CSV_PATH", tmp_path / "activities.csv")
    rows = A.normalise(_activity(1, 1)) + A.normalise(_activity(2, 1))
    rows[0]["type"], rows[1]["type"] = "Run", "Ride"
    monkeypatch.setattr(F, "fetch_feed", lambda: rows)

    assert F.run() == 2
    assert [r["type"] for r in _read(tmp_path / "activities.csv")] == ["Run", "Ride"]


@pytest.fixture
def member_paths(tmp_path, monkeypatch):
    paths = {"csv": tmp_path / "members.csv", "count": tmp_path / "member_count.csv"}
    monkeypatch.setattr(M, "CSV_PATH", paths["csv"])
    monkeypatch.setattr(M, "COUNT_PATH", paths["count"])
    return paths


def _members(path):
    return {r["athlete_id"]: (r["name"], r["ingest_at"], r["left_at"]) for r in _read(path)}


def test_tallied_roster_adds_marks_leavers_and_rejoins(member_paths):
    assert M.update_members(2, {"1": "Alice", "2": "Bob"}, "T1") == ["1", "2"]
    assert M.update_members(2, {"1": "Alice A.", "3": "Cara"}, "T2") == ["3"]          # Bob left, Cara joined
    assert _members(member_paths["csv"]) == {"1": ("Alice", "T1", ""),                  # name kept from ingest
                                             "2": ("Bob", "T1", "T2"),
                                             "3": ("Cara", "T2", "")}
    assert M.update_members(3, {"1": "Alice", "2": "Bob", "3": "Cara"}, "T3") == []    # Bob back
    assert _members(member_paths["csv"])["2"] == ("Bob", "T1", "")


def test_untallied_roster_adds_newcomers_but_marks_nobody_left(member_paths, capsys):
    M.update_members(2, {"1": "Alice", "2": "Bob"}, "T1")
    assert M.update_members(5, {"1": "Alice", "3": "Cara"}, "T2") == ["3"]   # headline 5, roster 2: partial walk
    assert _members(member_paths["csv"])["2"] == ("Bob", "T1", "")
    assert "WARNING" in capsys.readouterr().out


def test_run_records_the_headline_and_retries_a_mismatch_once(member_paths, monkeypatch):
    answers = [(3, {"1": "A", "2": "B"}), (2, {"1": "A", "2": "B"})]   # someone left mid-walk, the retry tallies
    monkeypatch.setattr(M, "fetch_count_and_roster", lambda page: answers.pop(0))

    @contextmanager
    def _club_page(url):
        yield None
    monkeypatch.setattr(M, "club_page", _club_page)

    assert M.run() == ["1", "2"]
    assert answers == []
    assert [r["member_count"] for r in _read(member_paths["count"])] == ["2"]
    monkeypatch.setattr(M, "fetch_count_and_roster", lambda page: (2, {"1": "A", "2": "B"}))
    M.run()
    assert [r["member_count"] for r in _read(member_paths["count"])] == ["2", "2"]   # a history, one row a run
