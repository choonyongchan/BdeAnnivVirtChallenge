"""Integration test for the whole FormSG-export -> nominal_roll.csv conversion.

Feeds `convert()` a synthetic export (5 metadata lines, then the real header
row, then invented registrants) and checks the file it writes: the exact byte
shape the roll must have (UTF-8 BOM, LF, trailing newline), the rules applied
end to end (blank STRAVA username flagged, junk unit left blank, a repeat NRIC
deduped to the latest clean entry), and that a dropped duplicate's warnings are
*not* emitted.
"""
import csv
import io

import pytest

from src.nominal_roll.nominal_roll import convert

HEADER = [
    "Response timestamp", "[Myinfo] Name", "Type of service", "Unit", "Company",
    "Do you have a STRAVA account", "STRAVA User name", "SingPass Validated NRIC",
]

DATA_ROWS = [
    # clean
    ["09 Sep 2026 09:00:00 AM", "Alpha Tester", "Option 1 NSF", "41 SAR", "Hawk",
     "Yes", "Alpha Tester", "S001"],
    # no usable STRAVA username -> blank + WARN
    ["09 Sep 2026 09:05:00 AM", "Bravo Tester", "Option 2 REGULAR", "40 SAR", "Cougar",
     "No", "-", "S002"],
    # junk unit/company -> blank + WARN
    ["09 Sep 2026 09:10:00 AM", "Charlie Tester", "Option 1 NSF", "Singapore", "Nil",
     "Yes", "Charlie Tester", "S003"],
    # same NRIC, first entry unresolvable (WARN -> invalid)
    ["09 Sep 2026 09:15:00 AM", "Delta Tester", "Option 1 NSF", "Nil", "Nil",
     "Yes", "Delta Tester", "S004"],
    # same NRIC, later entry clean -> this one is kept
    ["09 Sep 2026 09:20:00 AM", "Delta Tester", "Option 1 NSF", "40 SAR", "Archer",
     "Yes", "Delta Tester", "S004"],
    [],  # all-blank row -> skipped
]


def _write_export(path, header=HEADER, data=DATA_ROWS):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for i in range(5):
            w.writerow([f"metadata line {i}"])
        w.writerow(header)
        w.writerows(data)
    return path


@pytest.fixture
def converted(tmp_path):
    in_path = _write_export(tmp_path / "export.csv")
    out_path = tmp_path / "nominal_roll.csv"
    count, notes = convert(in_path, out_path)
    return out_path, count, notes


def test_output_byte_shape(converted):
    out_path, _, _ = converted
    raw = out_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")        # UTF-8 BOM, matching the existing roll
    assert b"\r\n" not in raw                     # LF endings only
    assert raw.endswith(b"\n")                    # trailing newline
    assert raw.decode("utf-8-sig").splitlines()[0] == "Name,Unit,Company,Type of service,STRAVA username"


def test_rows_and_rules(converted):
    out_path, count, _ = converted
    assert count == 4                             # 5 data rows - 1 deduped, blank row skipped

    rows = [r for r in csv.reader(io.StringIO(out_path.read_bytes().decode("utf-8-sig"))) if r]
    data = rows[1:]
    assert [r[0] for r in data] == ["Alpha Tester", "Bravo Tester", "Charlie Tester", "Delta Tester"]

    assert data[0][1:4] == ["41SAR", "Hawk", "NSF"]
    assert data[1][1:3] == ["40SAR", "Cougar"] and data[1][4] == ""   # STRAVA username blanked
    assert data[2][1:3] == ["", ""]                                   # junk unit -> blank, never guessed
    assert data[3][1:3] == ["40SAR", "Archer"]                        # latest clean entry kept


def test_only_surviving_rows_warnings_are_reported(converted):
    _, _, notes = converted
    warn_names = {name for level, name, _ in notes if level == "WARN"}
    assert warn_names == {"Bravo Tester", "Charlie Tester"}           # Delta's dropped-row WARN not emitted
    assert any(level == "INFO" and "registered more than once" in msg
               for level, _, msg in notes)


def test_either_name_column_spelling_converts(tmp_path):
    """The export dropped the '[Myinfo] ' prefix; both spellings must still convert."""
    header = ["Name" if c == "[Myinfo] Name" else c for c in HEADER]
    in_path = _write_export(tmp_path / "plain.csv", header=header)
    count, _ = convert(in_path, tmp_path / "nominal_roll.csv")
    assert count == 4


def test_missing_name_column_aborts(tmp_path):
    bad_header = [c for c in HEADER if c != "[Myinfo] Name"]
    in_path = _write_export(tmp_path / "noname.csv", header=bad_header)
    with pytest.raises(SystemExit):
        convert(in_path, tmp_path / "out.csv")


def test_missing_source_column_aborts(tmp_path):
    bad_header = [c for c in HEADER if c != "Company"]
    in_path = _write_export(tmp_path / "bad.csv", header=bad_header)
    with pytest.raises(SystemExit):
        convert(in_path, tmp_path / "out.csv")


# The export repeats Unit/Company: a registrant's answer sits in one pair or the other.
DUP_HEADER = HEADER + ["Unit", "Company"]

DUP_ROWS = [
    # answered in the first pair
    ["16/9/2026 22:26", "Echo Tester", "Option 1 NSF", "40SAR", "COUGAR",
     "Yes", "Echo Tester", "S005", "", ""],
    # answered in the trailing pair
    ["17/9/2026 06:14", "Foxtrot Tester", "Option 1 NSF", "", "",
     "Yes", "Foxtrot Tester", "S006", "Stallion Pioneer Section 2", "Stallion"],
    # split across both pairs
    ["17/9/2026 10:33", "Golf Tester", "Option 2 REGULAR", "41SAR", "",
     "Yes", "Golf Tester", "S007", "", "Hawk"],
]


def _read(out_path):
    return [r for r in csv.reader(io.StringIO(out_path.read_bytes().decode("utf-8-sig"))) if r]


def test_duplicate_unit_company_columns(tmp_path):
    in_path = _write_export(tmp_path / "dup.csv", header=DUP_HEADER, data=DUP_ROWS)
    out_path = tmp_path / "nominal_roll.csv"
    convert(in_path, out_path)

    data = _read(out_path)[1:]
    assert [r[0] for r in data] == ["Echo Tester", "Foxtrot Tester", "Golf Tester"]
    assert [r[1:3] for r in data] == [["40SAR", "Cougar"], ["40SAR", "Stallion"],
                                      ["41SAR", "Hawk"]]


LATER_ROWS = [
    # brand new registrant -> appended
    ["09 Sep 2026 10:00:00 AM", "Echo Tester", "Option 1 NSF", "41 SAR", "Shrike",
     "Yes", "Echo Tester", "S005"],
    # Alpha re-registered -> replaces the row where it already sits
    ["09 Sep 2026 10:05:00 AM", "Alpha Tester", "Option 1 NSF", "8 SAB", "Nil",
     "Yes", "Alpha Tester", "S001"],
]


def test_merges_into_an_existing_roll(converted):
    """Exports are incremental, so a second one must grow the roll, not replace it."""
    out_path, _, _ = converted
    before = _read(out_path)

    in_path = _write_export(out_path.parent / "later.csv", data=LATER_ROWS)
    count, notes = convert(in_path, out_path)

    assert count == 5                                  # the 4 already there + 1 new
    rows = _read(out_path)
    assert [r[0] for r in rows[1:]] == ["Alpha Tester", "Bravo Tester", "Charlie Tester",
                                        "Delta Tester", "Echo Tester"]
    assert rows[1][1:3] == ["8SAB", ""]                # Alpha updated where it already sat
    assert rows[2:5] == before[2:5]                    # everyone else untouched
    assert rows[5][1:3] == ["41SAR", "Shrike"]
    assert out_path.read_bytes().startswith(b"\xef\xbb\xbf")   # byte shape unchanged
    assert any(level == "INFO" and "merged into the existing roll" in msg
               for level, _, msg in notes)
