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

from src.nominal_roll.parse_nominal_roll import convert

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


def test_missing_source_column_aborts(tmp_path):
    bad_header = [c for c in HEADER if c != "Company"]
    in_path = _write_export(tmp_path / "bad.csv", header=bad_header)
    with pytest.raises(SystemExit):
        convert(in_path, tmp_path / "out.csv")
