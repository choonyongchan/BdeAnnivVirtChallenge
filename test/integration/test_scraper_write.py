"""Integration test for the CSV writers (no browser): the ledger and members.csv are append-only and
deduped by id; existing member rows are never rewritten. Roll usernames join only when athlete search shows them
in the club."""
import csv
import json
from types import SimpleNamespace
from urllib.parse import unquote

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


def _source(athletes):
    return SimpleNamespace(athletes=athletes)


def test_members_write_is_append_only(member_paths):
    assert M.write_members(1000, [_source({"1": "Alice Anon"})]) == 1
    assert M.write_members(1001, [_source({"1": "Alice A."}), _source({"3": "Cara Cipher"})]) == 1   # Alice renamed
    assert M.write_members(1001, [_source({"3": "Cara Cipher"})]) == 0                               # nothing new

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

    assert R.run(board) == ["1", "2"]          # the new members' ids
    assert {r["athlete_id"] for r in _read(member_paths["csv"])} == {"1", "2"}
    assert [r["activity_id"] for r in _read(tmp_path / "activities.csv")] == ["1"]


class _SearchPage:
    """Answers athlete searches from {username: results}; None makes that search fail with HTTP 429."""

    def __init__(self, answers):
        self.answers = answers
        self.searched = []

    def evaluate(self, script, url):
        username = unquote(url.split("text=")[1])
        self.searched.append(username)
        results = self.answers.get(username, [])
        if results is None:
            return {"ok": False, "status": 429, "text": ""}
        data = {"props": {"pageProps": {"searchResults": results}}}
        return {"ok": True, "status": 200,
                "text": f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'}

    def wait_for_timeout(self, ms):
        pass


CLUB = "BDE ANNIVERSARY VIRTUAL CHALLENGE"


def _in_club(aid, name):
    return {"idStr": aid, "name": name, "analyticReasonCategory": "common_club",
            "subtitle": f"You and {name} are both in {CLUB}"}


@pytest.fixture
def roll(tmp_path, monkeypatch):
    path = tmp_path / "nominal_roll.csv"
    path.write_text("﻿Name,Unit,Company,Type of service,STRAVA username\n"
                    "ALICE ANON,40SAR,Cougar,NSF,alice  ANON\n"         # already a member, by name
                    "BOB BOGUS,41SAR,Falcon,NSF,Bob Bogus\n"
                    "CARA CIPHER,SBW,,NSF,Cara\n"
                    "DAVE DUMMY,8SAB,,NSF,\n", encoding="utf-8")          # no username: never searched
    monkeypatch.setattr(M, "ROLL_PATH", path)
    return path


def test_roll_members_are_searched_and_kept_only_when_in_the_club(member_paths, roll):
    M.append_members({"1": "Alice Anon"})
    page = _SearchPage({"bob bogus": [{"idStr": "9", "name": "Bob Bogus"}, _in_club("2", "Bob  B")],
                        "cara": [{"idStr": "8", "name": "Cara Elsewhere"}]})   # not in the club

    found = M.NominalRollMembers(page, CLUB)

    assert sorted(page.searched) == ["bob bogus", "cara"]
    assert found.athletes == {"2": "Bob  B"}   # in the club, whatever the name
    assert M.append_members(found.athletes) == ["2"]


def test_roll_search_failure_keeps_earlier_hits(member_paths, roll):
    page = _SearchPage({"alice anon": [_in_club("1", "Alice Anon")], "bob bogus": None})
    found = M.NominalRollMembers(page, CLUB)
    assert found.athletes == {"1": "Alice Anon"}
    assert found.searched == 1
    assert page.searched == ["alice anon", "bob bogus"]   # stopped before cara
