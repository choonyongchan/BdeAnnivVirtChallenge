"""Unit tests for the FormSG roster converter's rule engine.

These pin down *why* each free-text answer resolves the way it does — unit
backfill from a unit-exclusive company name, swapped Unit/Company boxes, the
bare-number "SAR suffix assumed" rule, junk answers left blank (never guessed),
and the keep-the-latest-clean-entry dedupe. Table-driven so a rule change
breaks exactly the cases it changes.
"""
from datetime import datetime

import pytest

from src.nominal_roll.parse_nominal_roll import (
    canon_company,
    clean_service,
    dedupe,
    entry_order,
    is_nil,
    parse_field,
    resolve,
    smart_title,
)

# (raw_unit, raw_company) -> (unit, company). Every pair is a real shape from
# the export; units/companies are org structure, not personal data.
RESOLVE_CASES = [
    # already-clean rows: must not regress
    (("41 SAR", "Hawk"),             ("41SAR", "Hawk")),
    (("40SAR", "ARCHER COY"),        ("40SAR", "Archer")),
    (("41 sar", "heron"),            ("41SAR", "Heron")),
    (("41 SAR", "Nil"),              ("41SAR", "")),
    # HQ alias -> the unit's HQ company
    (("40 SAR", "BN HQ"),            ("40SAR", "Hercules")),
    (("8 SAB", "HQ"),                ("8SAB", "")),          # 8SAB has no companies
    (("HQ 8 SAB", "S4 Branch"),      ("8SAB", "")),
    # backwards unit token, rescued by the unit-exclusive company name
    (("SAR41", "FALCON"),            ("41SAR", "Falcon")),
    (("A1157", "falcon"),            ("41SAR", "Falcon")),
    # SBW is checked before any embedded number
    (("SBW/ 4SAB", "NIL"),           ("SBW", "")),
    # unit comes only from the company / a phrase / the wrong box
    (("Nil", "Hawk company"),        ("41SAR", "Hawk")),
    (("HAWK", "41 SAR"),             ("41SAR", "Hawk")),     # boxes swapped
    (("Singapore", "Cougar"),        ("40SAR", "Cougar")),   # junk unit, company saves it
    (("Keat Hong camp", "Glory"),    ("41SAR", "Glory")),
    (("Heron", "Heron"),             ("41SAR", "Heron")),
    (("Singapore", "412 C COY"),     ("412SAR", "C Coy")),   # unit hidden in company field
    # the Unit field's bare number wins, even against another unit's company
    (("130", "HAWK"),                ("130SAR", "Hawk")),
    (("489", "Support"),             ("489SAR", "Support")),
    (("Singapore", "HQ/480"),        ("480SAR", "HQ")),
    # unknown-but-real units: no company list, pass the company through
    (("412 SAR", "Support"),         ("412SAR", "Support")),
    (("412SAR", "AIA"),              ("412SAR", "AIA")),
    (("489SAR", "A coy"),            ("489SAR", "A Coy")),
    (("HQ1784", "Coy A"),            ("HQ1784", "A Coy")),
    (("HQ1784", "Fabrica Robotics"), ("HQ1784", "Fabrica Robotics")),
    (("Mech Cluster", "Nil"),        ("Mech Cluster", "")),
    # unknown unit, canonicalised to one spelling
    (("campops", "nil"),             ("Campops", "")),
    (("Camp Ops", "Nil"),            ("Campops", "")),
    (("SATPOOL", "Nil"),             ("Satpool", "")),
    (("Satpool", "NIL"),             ("Satpool", "")),
    # nothing recoverable -> blank, never junk
    (("Singapore", "Nil"),           ("", "")),
    (("NA", "-"),                    ("", "")),
]


@pytest.mark.parametrize(
    "raw,expected",
    [pytest.param(r, e, id=f"{r[0]}|{r[1]}") for r, e in RESOLVE_CASES],
)
def test_resolve(raw, expected):
    unit, company, _ = resolve(*raw)
    assert (unit, company) == expected


def test_unresolvable_row_is_flagged():
    """A row we cannot resolve must be flagged, not silently blanked."""
    levels = {lvl for lvl, _ in resolve("Singapore", "Nil")[2]}
    assert "WARN" in levels


def test_clean_row_is_quiet():
    assert resolve("41 SAR", "Hawk")[2] == []


def test_backfill_emits_info_note():
    """Correcting the unit from the company is reported, not hidden."""
    levels = {lvl for lvl, _ in resolve("Singapore", "Cougar")[2]}
    assert "INFO" in levels and "WARN" not in levels


def test_bare_number_notes_suffix_assumed():
    notes = resolve("489", "Support")[2]
    assert any(lvl == "INFO" and "SAR suffix assumed" in msg for lvl, msg in notes)


@pytest.mark.parametrize("raw,unit,leftover", [
    ("40 SAR", "40SAR", ""),
    ("HQ 8 SAB", "8SAB", "HQ"),
    ("489", "489SAR", ""),
    ("SBW/ 4SAB", "SBW", "4SAB"),
    ("Keat Hong camp 8sab", "8SAB", "Keat Hong camp"),
    ("just words", "", "just words"),
])
def test_parse_field(raw, unit, leftover):
    assert parse_field(raw) == (unit, leftover)


@pytest.mark.parametrize("unit", ["8SAB", "SBW"])
def test_canon_company_blank_for_companyless_units(unit):
    assert canon_company("Anything", unit) == ("", None)


def test_canon_company_single_letter_becomes_coy():
    assert canon_company("C", "412SAR") == ("C Coy", None)


def test_canon_company_unknown_company_warns():
    company, note = canon_company("Falcon", "40SAR")  # Falcon is a 41SAR company
    assert company == "Falcon" and note[0] == "WARN"


# (description, entries, expected kept rows) — entries are (key, order, valid, row)
DEDUPE_CASES = [
    ("latest of two valid entries wins",
     [("S1", 1, True, ["ANN", "41SAR"]), ("S1", 2, True, ["ANN", "40SAR"])],
     [["ANN", "40SAR"]]),
    ("a valid entry beats a later invalid one",
     [("S1", 1, True, ["ANN", "41SAR"]), ("S1", 2, False, ["ANN", ""])],
     [["ANN", "41SAR"]]),
    ("with nothing valid, the latest entry is kept",
     [("S1", 2, False, ["ANN", ""]), ("S1", 1, False, ["ANN", "Nil"])],
     [["ANN", ""]]),
    ("different people are all kept, in file order",
     [("S1", 1, True, ["ANN", "41SAR"]), ("S2", 2, True, ["BOB", "40SAR"])],
     [["ANN", "41SAR"], ["BOB", "40SAR"]]),
    ("a kept entry stays at the position of the person's first row",
     [("S1", 1, False, ["ANN", ""]), ("S2", 2, True, ["BOB", "40SAR"]),
      ("S1", 3, True, ["ANN", "41SAR"])],
     [["ANN", "41SAR"], ["BOB", "40SAR"]]),
]


@pytest.mark.parametrize(
    "entries,expected",
    [pytest.param(e, x, id=d) for d, e, x in DEDUPE_CASES],
)
def test_dedupe_keeps_the_right_row(entries, expected):
    rows, _ = dedupe(entries)
    assert rows == expected


@pytest.mark.parametrize(
    "entries", [e for d, e, x in DEDUPE_CASES[:3]], ids=[d for d, e, x in DEDUPE_CASES[:3]]
)
def test_dedupe_reports_the_dropped_entry(entries):
    """Whenever a duplicate is dropped, that must be recorded."""
    assert dedupe(entries)[1]


@pytest.mark.parametrize("value,nil", [
    ("", True), ("  ", True), ("-", True), ("NIL", True), ("n/a", True), ("None", True),
    ("Hawk", False), ("0", False),
])
def test_is_nil(value, nil):
    assert is_nil(value) is nil


@pytest.mark.parametrize("raw,titled", [
    ("keat hong", "Keat Hong"),
    ("HQ", "HQ"),               # short acronym left alone
    ("412SAR", "412SAR"),       # digit-bearing word left alone
    ("archer coy", "Archer Coy"),
])
def test_smart_title(raw, titled):
    assert smart_title(raw) == titled


@pytest.mark.parametrize("raw,clean", [
    ("Option 1 NSF", "NSF"),
    ("Option 12 REGULAR", "REGULAR"),
    ("NSF", "NSF"),
])
def test_clean_service(raw, clean):
    assert clean_service(raw) == clean


def test_entry_order_parses_timestamp_and_falls_back():
    parsed = entry_order("09 Sep 2026 01:02:03 PM", 5)
    assert isinstance(parsed, datetime) and parsed.year == 2026
    # unparseable -> deterministic file-order fallback, later index sorts later
    assert entry_order("not a date", 2) < entry_order("still not", 3)
