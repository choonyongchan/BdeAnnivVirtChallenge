"""Unit tests for roll discovery's pure helpers: name_ok() gates search hits; candidates()/is_due()
decide which roll usernames get (re)searched."""
from datetime import datetime, timedelta, timezone

import pytest

from src.nominal_roll import nominal_roll as D

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def _entry(status, days_ago=0, roll_name="A B"):
    return {"status": status, "roll_name": roll_name,
            "checked_at": (NOW - timedelta(days=days_ago)).isoformat()}


def _row(username, name="A B"):
    return {"Name": name, "STRAVA username": username}


def test_name_ok_accepts_username_or_real_name():
    assert D.name_ok("Tan See Kiat", "TAN SEE KIAT", "whatever")
    assert D.name_ok("Kiat See Tan", "X", "Tan See Kiat")           # word order
    assert D.name_ok("Vilmus Lee", "LEE SZE RUI, VILMUS", "Vilmus")  # word subset of the real name


def test_name_ok_rejects_different_person():
    assert not D.name_ok("Sidharth Praveen", "CHENDIL PRAVEEN", "Praveen")
    assert not D.name_ok("Darren Ho", "WARREN HO", "Warren Ho")


@pytest.mark.parametrize("entry, roll_name, due", [
    (None, "A B", True),                                    # never searched
    (_entry("member"), "A B", False),                       # already in the ledger
    (_entry("not_in_club", days_ago=6), "A B", False),      # re-check only after a week
    (_entry("private", days_ago=7), "A B", True),
    (_entry("no_result", days_ago=30), "A B", False),       # only when the roll row changes
    (_entry("name_mismatch", days_ago=1), "A C", True),
])
def test_is_due(entry, roll_name, due):
    assert D.is_due(entry, roll_name, NOW) is due


def test_candidates_skips_ledger_blank_and_settled():
    roll = [_row("Alice Lee"), _row(""), _row("Bob Tan"), _row("New Guy")]
    state = {"Bob Tan": _entry("not_in_club", days_ago=1)}
    got = D.candidates(roll, {"alice lee"}, state, NOW)
    assert [r["STRAVA username"] for r in got] == ["New Guy"]


def test_trigger_rows_new_or_changed_username_only():
    before = [["AL", "40SAR", "", "NSF", "al_old"], ["BO", "40SAR", "", "NSF", "bo"], ["CY", "40SAR", "", "NSF", "cy"]]
    after = [["AL", "40SAR", "", "NSF", "al_new"],      # username changed
             ["BO", "41SAR", "", "NSF", "bo"],          # re-registered, same username
             ["CY", "40SAR", "", "NSF", ""],            # username removed
             ["DI", "40SAR", "", "NSF", "di"],          # new person
             ["EE", "40SAR", "", "NSF", ""]]            # new person, no username
    assert [r["STRAVA username"] for r in D.trigger_rows(before, after)] == ["al_new", "di"]
