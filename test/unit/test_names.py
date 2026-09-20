"""Unit tests for roster loading and name resolution.

Self-reported STRAVA usernames rarely match the Strava display name exactly, so
resolution normalises both sides, then falls back to word-order-insensitive,
fuzzy, and finally real-name matching fitted globally by fit() -- which must
stay strictly one-to-one and must refuse to guess when several roster entries
fit equally well.
Company is stored unit-qualified; a couple of employer names typed into the
Company field are scrubbed to blank.
"""
import csv

import pytest

from src.dashboard.names import NominalRoll


def _write_roll(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Unit", "Company", "Type of service", "STRAVA username"])
        w.writerows(rows)


def test_resolve_matches_regardless_of_case(roll):
    assert roll.resolve("Alice Anon") == "ALICE ANON"
    assert roll.resolve("alice anon") == "ALICE ANON"


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


# --- normalisation, word order, fuzziness, and the one-to-one guarantee -------


def _fitted(tmp_path, monkeypatch, rows, names):
    """A NominalRoll built from `rows` and fitted over the raw Strava `names`."""
    p = tmp_path / "nominal_roll.csv"
    _write_roll(p, rows)
    monkeypatch.setattr(NominalRoll, "CSV_PATH", p)
    monkeypatch.setattr("src.dashboard.names.REPORT_PATH", tmp_path / "report.log")
    roll = NominalRoll()
    roll.fit(names)
    return roll


@pytest.mark.parametrize("raw", ["Alice  Anon", "Alice .Anon", "  ALICE   anon  ", "Alïce Anon"])
def test_cosmetic_differences_still_match(roll, raw):
    """Doubled spaces, stray punctuation and accents are not real differences."""
    assert roll.resolve(raw) == "ALICE ANON"


def test_reversed_word_order_matches(tmp_path, monkeypatch):
    roll = _fitted(tmp_path, monkeypatch,
                   [["CHEONG CHENG KANG", "40SAR", "Cougar", "NSF", "Cheong Cheng Kang"]],
                   ["Cheng Kang Cheong"])
    assert roll.resolve("Cheng Kang Cheong") == "CHEONG CHENG KANG"


def test_typo_above_threshold_matches(tmp_path, monkeypatch):
    """One transposed letter in the roll must not cost the athlete their runs."""
    roll = _fitted(tmp_path, monkeypatch,
                   [["NOOR SHAHFYZAD", "40SAR", "Cougar", "NSF", "Noir Shahfyzad"]],
                   ["Noor Shahfyzad"])
    assert roll.resolve("Noor Shahfyzad") == "NOOR SHAHFYZAD"


def test_different_people_below_threshold_do_not_match(tmp_path, monkeypatch):
    """"Darren Ho" vs "Warren Ho" scores 0.89 - close, but two different people."""
    roll = _fitted(tmp_path, monkeypatch,
                   [["WARREN HO YI LUN", "40SAR", "Cougar", "NSF", "Warren Ho"]],
                   ["Darren Ho"])
    assert roll.resolve("Darren Ho") == "Darren Ho"


def test_match_is_one_to_one(tmp_path, monkeypatch):
    """Two Strava accounts must never both be credited to the same person."""
    roll = _fitted(tmp_path, monkeypatch,
                   [["ALICE ANON", "40SAR", "Cougar", "NSF", "Alice Anon"]],
                   ["Alice Anon", "Alice Anonn"])
    resolved = [roll.resolve("Alice Anon"), roll.resolve("Alice Anonn")]
    assert resolved.count("ALICE ANON") == 1
    assert len(set(roll.match_map.values())) == len(roll.match_map)


def test_username_claimed_by_two_people_matches_neither(tmp_path, monkeypatch):
    """No rule can pick an owner, so guessing would credit the wrong person."""
    roll = _fitted(tmp_path, monkeypatch, [
        ["HOO JUN HAO", "40SAR", "Cougar", "NSF", "Jun Hao"],
        ["CHIN JUN HAO", "41SAR", "Hawk", "NSF", "jun hao"],
    ], ["Jun Hao"])
    assert roll.resolve("Jun Hao") == "Jun Hao"
    assert roll.conflicts["jun hao"] == ["CHIN JUN HAO", "HOO JUN HAO"]


def test_contested_name_is_not_rescued_by_the_fuzzy_tier(tmp_path, monkeypatch):
    """A blocked name must not fall through to fuzzy; here two Jun Haos also tie
    on their real names, so the real-name tier refuses it too."""
    roll = _fitted(tmp_path, monkeypatch, [
        ["HOO JUN HAO", "40SAR", "Cougar", "NSF", "Jun Hao"],
        ["CHIN JUN HAO", "41SAR", "Hawk", "NSF", "jun hao"],
        ["ALICE ANON", "40SAR", "Cougar", "NSF", "Alice Anon"],
    ], ["Jun Hao", "jun hao", "Alice Anon"])
    assert roll.match_map == {"Alice Anon": "ALICE ANON"}


def test_fit_writes_a_report(tmp_path, monkeypatch):
    report = tmp_path / "report.log"
    p = tmp_path / "nominal_roll.csv"
    _write_roll(p, [["NOOR SHAHFYZAD", "40SAR", "Cougar", "NSF", "Noir Shahfyzad"]])
    monkeypatch.setattr(NominalRoll, "CSV_PATH", p)
    monkeypatch.setattr("src.dashboard.names.REPORT_PATH", report)
    NominalRoll().fit(["Noor Shahfyzad", "Someone Else"])
    text = report.read_text(encoding="utf-8")
    assert "NOOR SHAHFYZAD" in text and "Someone Else" in text


def test_resolve_works_without_fit(roll):
    """The pipeline stays sane if fit() was never called."""
    assert roll.match_map == {}
    assert roll.resolve("Bob Bogus") == "BOB BOGUS"
    assert roll.resolve("Nobody Here") == "Nobody Here"


def test_non_latin_username_survives_normalisation(tmp_path, monkeypatch):
    """A CJK username must stay matchable, not be stripped to an empty key."""
    roll = _fitted(tmp_path, monkeypatch,
                   [["LIN CHING HUA", "40SAR", "Cougar", "NSF", "林敬樺"]],
                   ["林敬樺"])
    assert roll.resolve("林敬樺") == "LIN CHING HUA"


def test_punctuation_only_username_is_ignored(tmp_path, monkeypatch):
    """"." carries no signal; it must not become a key others collide with."""
    roll = _fitted(tmp_path, monkeypatch, [
        ["FIRST DOTTED", "40SAR", "Cougar", "NSF", "."],
        ["SECOND DOTTED", "41SAR", "Hawk", "NSF", "。。"],
    ], ["."])
    assert roll.conflicts == {}
    assert roll.resolve(".") == "."


# --- real-name tier -------------------------------------------------------
# Matches the Strava display name against the roll's Name column, for people
# whose self-reported username was wrong, blank, or claimed by someone else.


def test_blank_username_still_matches_on_the_real_name(tmp_path, monkeypatch):
    """Leaving the username off the form must not cost the athlete their runs."""
    roll = _fitted(tmp_path, monkeypatch,
                   [["MUHAMMAD SHUAAR BIN ZAKIR HUSSAIN", "41SAR", "Hawk", "NSF", ""]],
                   ["Muhd Shuaar"])
    assert roll.resolve("Muhd Shuaar") == "MUHAMMAD SHUAAR BIN ZAKIR HUSSAIN"


def test_real_name_may_be_shorter_than_the_strava_name(tmp_path, monkeypatch):
    """An English name on Strava that the roll never recorded is not a mismatch."""
    roll = _fitted(tmp_path, monkeypatch,
                   [["LEE SEN YEW", "40SAR", "Cougar", "NSF", ""]],
                   ["Dylan Lee Sen Yew"])
    assert roll.resolve("Dylan Lee Sen Yew") == "LEE SEN YEW"


def test_rank_and_serial_prefixes_are_not_part_of_the_name(tmp_path, monkeypatch):
    """People prefix a rank or a four-digit serial; neither names a person."""
    roll = _fitted(tmp_path, monkeypatch, [
        ["LIM POH KIAT JETHRO", "41SAR", "Hawk", "NSF", ""],
        ["PRAVIN KUHANESON", "41SAR", "Hawk", "NSF", ""],
    ], ["1111 Jethro lim", "2416 REC Pravin K"])
    assert roll.resolve("1111 Jethro lim") == "LIM POH KIAT JETHRO"
    assert roll.resolve("2416 REC Pravin K") == "PRAVIN KUHANESON"


def test_contested_username_is_rescued_by_an_unambiguous_real_name(tmp_path, monkeypatch):
    """Two people claiming one username says nothing about whose real name is whose."""
    roll = _fitted(tmp_path, monkeypatch, [
        ["JONAS LIM SHI XIANG", "40SAR", "Cougar", "NSF", "Jonas Lim"],
        ["LIM YAN REN", "41SAR", "Hawk", "NSF", "jonas lim"],
    ], ["Jonas Lim"])
    assert roll.conflicts["jonas lim"] == ["JONAS LIM SHI XIANG", "LIM YAN REN"]
    assert roll.resolve("Jonas Lim") == "JONAS LIM SHI XIANG"


def test_two_roster_entries_fitting_equally_well_match_neither(tmp_path, monkeypatch):
    """Guessing between two Jun Haos would credit one man's runs to the other."""
    roll = _fitted(tmp_path, monkeypatch, [
        ["HOO JUN HAO", "40SAR", "Cougar", "NSF", ""],
        ["CHIN JUN HAO", "41SAR", "Hawk", "NSF", ""],
    ], ["Jun Hao"])
    assert roll.resolve("Jun Hao") == "Jun Hao"


def test_single_word_name_is_too_little_to_go_on(tmp_path, monkeypatch):
    """One word names too many people to count as evidence, even when unique."""
    roll = _fitted(tmp_path, monkeypatch,
                   [["TAN WEI JIE JETHRO", "40SAR", "Cougar", "NSF", ""]],
                   ["Jethro"])
    assert roll.resolve("Jethro") == "Jethro"


def test_report_lists_refused_real_name_candidates(tmp_path, monkeypatch):
    """A refusal must be visible in the audit trail, not silent."""
    report = tmp_path / "report.log"
    p = tmp_path / "nominal_roll.csv"
    _write_roll(p, [["HOO JUN HAO", "40SAR", "Cougar", "NSF", ""],
                    ["CHIN JUN HAO", "41SAR", "Hawk", "NSF", ""]])
    monkeypatch.setattr(NominalRoll, "CSV_PATH", p)
    monkeypatch.setattr("src.dashboard.names.REPORT_PATH", report)
    NominalRoll().fit(["Jun Hao"])
    text = report.read_text(encoding="utf-8")
    assert "ambiguous real-name candidates" in text
    assert "CHIN JUN HAO | HOO JUN HAO" in text
