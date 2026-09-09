"""Integration test for the two scrapers' CSV merge steps (no browser).

Only `write()` is exercised — the Playwright `fetch()` is not. The rules:
activities.csv is append-only and deduped by activity_id (header written once,
scraped_at stamped), and members.csv is a ledger where a returning athlete
keeps their original first_seen, a renamed athlete's row is updated, and an
athlete who has left is never removed.
"""
import csv
from datetime import datetime

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

    A.ActivityScraper().write([_activity(1, 7)])
    A.ActivityScraper().write([_activity(1, 7), _activity(2, 8)])   # 1 repeats, 2 is new

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
    A.ActivityScraper().write([idless, group])

    assert [r["activity_id"] for r in _read(path)] == ["10", "11"]


class _FrozenClock:
    """Stand-in for the module's `datetime`, so scrape timestamps are deterministic."""
    def __init__(self, iso):
        self._iso = iso

    def now(self, tz=None):
        return datetime.fromisoformat(self._iso)


def test_members_ledger_keeps_first_seen_updates_name_and_never_deletes(tmp_path, monkeypatch):
    path = tmp_path / "members.csv"
    monkeypatch.setattr(M, "CSV_PATH", path)

    monkeypatch.setattr(M, "datetime", _FrozenClock("2026-09-10T00:00:00+00:00"))
    M.MemberScraper().write({"1": "Alice Anon", "2": "Bob Bogus"})
    first = {r["athlete_id"]: r for r in _read(path)}
    assert first["1"]["first_seen"] == first["1"]["last_seen"] == "2026-09-10T00:00:00+00:00"

    monkeypatch.setattr(M, "datetime", _FrozenClock("2026-09-20T00:00:00+00:00"))
    M.MemberScraper().write({"1": "Alice A.", "3": "Cara Cipher"})   # Alice renamed, Bob gone, Cara new
    rows = _read(path)
    by_id = {r["athlete_id"]: r for r in rows}

    assert by_id["1"]["name"] == "Alice A."
    assert by_id["1"]["first_seen"] == "2026-09-10T00:00:00+00:00"   # original, untouched
    assert by_id["1"]["last_seen"] == "2026-09-20T00:00:00+00:00"    # bumped
    assert by_id["2"]["last_seen"] == "2026-09-10T00:00:00+00:00"    # Bob retained, frozen
    assert "3" in by_id                                              # Cara added
    assert [r["name"].lower() for r in rows] == sorted(r["name"].lower() for r in rows)
