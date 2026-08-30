"""Table-driven checks for resolve(); run with `pytest data/test_build_nominal_roll.py`.

Every case is a real (Unit, Company) pair from the FormSG export.
"""
import pytest

from build_nominal_roll import dedupe, resolve

# (raw_unit, raw_company) -> (unit, company)
CASES = [
    # --- rows that already parsed correctly: these must not regress ---
    (("41 SAR", "Hawk"),            ("41SAR", "Hawk")),
    (("40 SAR", "BN HQ"),           ("40SAR", "Hercules")),
    (("40SAR", "ARCHER COY"),       ("40SAR", "Archer")),
    (("41 sar", "heron"),           ("41SAR", "Heron")),
    (("SAR41", "FALCON"),           ("41SAR", "Falcon")),
    (("SBW/ 4SAB", "NIL"),          ("SBW", "")),
    (("8 SAB", "HQ"),               ("8SAB", "")),
    (("HQ 8 SAB", "S4 Branch"),     ("8SAB", "")),
    (("8 sab ssp", "8 sab ssp"),    ("8SAB", "")),
    (("41SAR S4 Branch", "Heron"),  ("41SAR", "Heron")),
    (("Keat Hong camp", "Glory"),   ("41SAR", "Glory")),
    (("A1157", "falcon"),           ("41SAR", "Falcon")),
    (("Singapore", "Cougar"),       ("40SAR", "Cougar")),
    (("Heron", "Heron"),            ("41SAR", "Heron")),
    (("412 SAR", "Support"),        ("412SAR", "Support")),
    (("412SAR", "AIA"),             ("412SAR", "AIA")),
    (("41 SAR", "Nil"),             ("41SAR", "")),

    # --- unit backfilled from a company written as a phrase (R'yan Alfian) ---
    (("Nil", "Hawk company"),       ("41SAR", "Hawk")),

    # --- unit and company swapped (Farhat Meah) ---
    (("HAWK", "41 SAR"),            ("41SAR", "Hawk")),

    # --- unit hidden inside the company field (Kho Jin Yao) ---
    (("Singapore", "412 C COY"),    ("412SAR", "C Coy")),
    (("489SAR", "A coy"),           ("489SAR", "A Coy")),
    (("HQ1784", "Coy A"),           ("HQ1784", "A Coy")),

    # --- unknown but real units, canonicalised to one spelling (Lee Ming, Aranesh) ---
    (("campops", "nil"),            ("Campops", "")),
    (("Camp Ops", "Nil"),           ("Campops", "")),
    (("SATPOOL", "Nil"),            ("Satpool", "")),
    (("Satpool", "NIL"),            ("Satpool", "")),
    (("SSP", "NIL"),                ("SSP", "")),
    (("Mech Cluster", "Nil"),       ("Mech Cluster", "")),
    (("HQ1784", "Fabrica Robotics"), ("HQ1784", "Fabrica Robotics")),

    # --- a bare number means the SAR suffix was left off, in either field ---
    (("489", "Support"),            ("489SAR", "Support")),
    (("Singapore", "HQ/480"),       ("480SAR", "HQ")),
    # a number in the Unit field is a real unit, even when the company belongs to another
    (("130", "HAWK"),               ("130SAR", "Hawk")),

    # --- nothing recoverable from either field: blank, never junk ---
    (("Singapore", "Nil"),          ("", "")),
    (("NA", "-"),                   ("", "")),
]


# (description, (key, order, valid, row) in file order, the names kept in order)
DEDUPE_CASES = [
    ("the latest of two valid entries wins",
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

DEDUPE_PARAMS = [pytest.param(entries, expected, id=description)
                 for description, entries, expected in DEDUPE_CASES]
# The first three cases are the ones where a duplicate is actually dropped.
DEDUPE_DROP_PARAMS = DEDUPE_PARAMS[:3]


def levels(notes):
    return {level for level, _ in notes}


@pytest.mark.parametrize("raw,expected", [pytest.param(raw, expected, id=f"{raw[0]}|{raw[1]}")
                                          for raw, expected in CASES])
def test_resolve(raw, expected):
    unit, company, _ = resolve(*raw)
    assert (unit, company) == expected


def test_unresolvable_row_is_flagged():
    """An unresolvable row must be flagged, not silently blanked."""
    assert "WARN" in levels(resolve("Singapore", "Nil")[2])


def test_clean_row_is_quiet():
    assert resolve("41 SAR", "Hawk")[2] == []


@pytest.mark.parametrize("entries,expected", DEDUPE_PARAMS)
def test_dedupe(entries, expected):
    rows, _ = dedupe(entries)
    assert rows == expected


@pytest.mark.parametrize("entries,expected", DEDUPE_DROP_PARAMS)
def test_dedupe_reports_dropped_entry(entries, expected):
    """Dropping a duplicate must be reported, whichever of the two entries won."""
    assert dedupe(entries)[1]
