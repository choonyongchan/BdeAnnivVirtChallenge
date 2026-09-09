"""Unit tests for roster loading and name resolution.

Strava's API truncates a display name to "<first words> <initial>.", so the
roll registers every such truncation of the STRAVA-username column and maps it
back to the full formal name. Company is stored unit-qualified; a couple of
employer names typed into the Company field are scrubbed to blank.
"""
import csv

import pytest

from src.dashboard.names import NominalRoll


def _write_roll(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Unit", "Company", "Type of service", "STRAVA username"])
        w.writerows(rows)


def test_all_truncations_enumerates_api_forms():
    assert list(NominalRoll._all_truncations("choon yong chan")) == [
        "choon yong chan", "choon y.", "choon yong c.",
    ]


def test_all_truncations_single_word_is_just_itself():
    assert list(NominalRoll._all_truncations("Madonna")) == ["madonna"]


def test_resolve_matches_full_and_truncated_forms(roll):
    assert roll.resolve("Alice Anon") == "ALICE ANON"
    assert roll.resolve("alice anon") == "ALICE ANON"
    assert roll.resolve("Alice A.") == "ALICE ANON"


def test_resolve_returns_unknown_names_unchanged(roll):
    assert roll.resolve("Nobody Here") == "Nobody Here"
    assert roll.resolve("   ") == ""


def test_unit_company_is_unit_qualified(roll):
    assert roll.unit_company("ALICE ANON") == {
        "unit": "40SAR", "company": "40SAR/Cougar", "service": "NSF",
    }


def test_company_blank_when_roll_names_none(roll):
    assert roll.unit_company("CARA CIPHER")["company"] == ""


@pytest.mark.parametrize("name,service", [
    ("ALICE ANON", "NSF"), ("BOB BOGUS", "REGULAR"),
    ("CARA CIPHER", "ALUMNI"), ("Nobody", ""),
])
def test_service_is_upper_cased(roll, name, service):
    assert roll.service(name) == service


def test_employer_typed_into_company_field_is_scrubbed(tmp_path, monkeypatch):
    p = tmp_path / "nominal_roll.csv"
    _write_roll(p, [["FAKE PERSON", "412SAR", "AIA", "NSF", "Fake Person"]])
    monkeypatch.setattr(NominalRoll, "CSV_PATH", p)
    assert NominalRoll().unit_company("FAKE PERSON")["company"] == ""


def test_row_without_strava_username_still_feeds_unit_company(tmp_path, monkeypatch):
    p = tmp_path / "nominal_roll.csv"
    _write_roll(p, [["NO STRAVA", "40SAR", "Archer", "NSF", ""]])
    monkeypatch.setattr(NominalRoll, "CSV_PATH", p)
    roll = NominalRoll()
    assert roll.unit_company("NO STRAVA")["unit"] == "40SAR"
    assert roll.name_map == {}                       # nothing to resolve from


def test_missing_csv_yields_empty_maps(tmp_path, monkeypatch):
    monkeypatch.setattr(NominalRoll, "CSV_PATH", tmp_path / "nope.csv")
    roll = NominalRoll()
    assert roll.name_map == {} and roll.unit_company_map == {}
    assert roll.resolve("Anyone") == "Anyone"
