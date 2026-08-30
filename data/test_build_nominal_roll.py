"""Table-driven checks for resolve(); run with `python data/test_build_nominal_roll.py`.

Every case is a real (Unit, Company) pair from the FormSG export.
"""
import sys

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


# (key, order, valid, row) in file order -> the names kept, in order
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


def levels(notes):
    return {level for level, _ in notes}


def main():
    failures = []
    for (raw_unit, raw_company), expected in CASES:
        unit, company, _ = resolve(raw_unit, raw_company)
        if (unit, company) != expected:
            failures.append(f"  ({raw_unit!r}, {raw_company!r}) -> "
                            f"{(unit, company)!r}, expected {expected!r}")

    # An unresolvable row must be flagged, not silently blanked.
    if "WARN" not in levels(resolve("Singapore", "Nil")[2]):
        failures.append("  ('Singapore', 'Nil') was blanked without a WARN")
    # A clean row must be quiet.
    if resolve("41 SAR", "Hawk")[2]:
        failures.append("  ('41 SAR', 'Hawk') produced notes but is unambiguous")

    for description, entries, expected in DEDUPE_CASES:
        rows, _ = dedupe(entries)
        if rows != expected:
            failures.append(f"  dedupe: {description}: got {rows!r}, expected {expected!r}")

    # Dropping a duplicate must be reported, whichever of the two entries won.
    for description, entries, _ in DEDUPE_CASES[:3]:
        if not dedupe(entries)[1]:
            failures.append(f"  dedupe: {description}: dropped an entry without a note")

    if failures:
        print(f"FAIL: {len(failures)} of {len(CASES) + len(DEDUPE_CASES) + 5} checks\n" + "\n".join(failures))
        return 1
    print(f"ok: {len(CASES) + len(DEDUPE_CASES) + 5} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
