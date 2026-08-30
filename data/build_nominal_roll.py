"""Convert the raw FormSG registration export into the cleaned data/nominal_roll.csv.

Autocorrects free-text Unit and Company answers, and flags anything it cannot
confidently convert. Also regenerates data/NOMINAL_ROLL_B64.txt, the copy-paste
source for the NOMINAL_ROLL_B64 GitHub secret that CI decodes at run time.
"""
import argparse
import base64
import csv
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

# Companies are unit-exclusive — that is what makes the company->unit backfill sound.
UNIT_COMPANIES = {
    "40SAR": ["Archer", "Braves", "Cougar", "Stallion", "Hercules"],  # Hercules = HQ coy
    "41SAR": ["Falcon", "Glory", "Hawk", "Shrike", "Heron"],          # Heron    = HQ coy
    "8SAB":  [],   # brigade HQ — no company
    "SBW":   [],   # Supply Base West — no company
}
HQ_COMPANY = {"40SAR": "Hercules", "41SAR": "Heron"}
COMPANIES = {c.lower(): (c, u) for u, coys in UNIT_COMPANIES.items() for c in coys}

NIL_VALUES = {"", "-", "na", "n/a", "nil", "none"}
JUNK_UNITS = NIL_VALUES | {"singapore"}   # answers that name no unit at all
HQ_ALIASES = {"hq", "bn hq"}

# Units smart_title() alone cannot spell consistently. Keyed on lowercase alphanumerics.
UNIT_ALIASES = {"campops": "Campops"}

OUTPUT_HEADER = ["Name", "Unit", "Company", "Type of service", "STRAVA username"]

# Source columns in the FormSG export, in output order.
SOURCE_COLUMNS = ["[Myinfo] Name", "Unit", "Company", "Type of service", "STRAVA User name"]

HEADER_ROW = 5  # the export prefixes 5 metadata lines before the real header


def is_nil(value: str) -> bool:
    return value.strip().lower() in NIL_VALUES


def parse_field(text: str) -> tuple:
    """Split one free-text answer into (unit, leftover).

    People put unit text, company text, or both, in either form field, so both fields
    go through this. 'leftover' is whatever text the unit match did not consume.
    """
    s = text.upper().replace("/", " ")
    unit, span = "", None

    # SBW (Supply Base West) is a real unit that simply isn't numbered. Checked first so
    # 'SBW/ 4SAB' resolves to SBW rather than the embedded 4SAB.
    m = re.search(r"\bSBW\b|SUPPLY BASE WEST", s)
    if m:
        unit, span = "SBW", m.span()

    # '40 SAR', 'HQ 8 SAB', 'S2 Br 8SAB', 'Keat Hong camp 8sab' -> digits followed by letters
    if not unit:
        m = re.search(r"(\d+)\s*([A-Z]{3,})", s)
        if m:
            unit, span = m.group(1) + m.group(2), m.span()

    # A standalone number means the default SAR suffix was left off: '489', 'HQ/480'.
    if not unit:
        m = re.search(r"\b(\d{2,3})\b", s)
        if m:
            unit, span = m.group(1) + "SAR", m.span()

    leftover = text if span is None else text[:span[0]] + text[span[1]:]
    return unit, leftover.strip(" /,-")


def company_name(text: str) -> str:
    """Strip the 'coy'/'company' wrapper people write around a company name."""
    return re.sub(r"\b(COY\.?|COMPANY)\b", " ", text, flags=re.IGNORECASE).strip()


def smart_title(text: str) -> str:
    """Title-case words but leave acronyms and anything containing a digit alone."""
    def word(w):
        if any(ch.isdigit() for ch in w) or (w.isupper() and len(w) <= 4):
            return w
        return w[:1].upper() + w[1:].lower()
    return " ".join(word(w) for w in text.split())


def canon_unit(text: str) -> str:
    """One spelling for a unit that isn't SAR/SAB numbered: 'campops', 'Camp Ops' -> 'Campops'."""
    return UNIT_ALIASES.get(re.sub(r"[^a-z0-9]", "", text.lower()), smart_title(text))


def canon_company(text: str, unit: str) -> tuple:
    """Returns (company, note). Companies are canonicalised against the unit's own list."""
    company = company_name(text)
    if is_nil(company) or unit in ("8SAB", "SBW"):
        return "", None

    if len(company) == 1 and company.isalpha():   # '412 C COY', 'Coy A' -> 'C Coy'
        return company.upper() + " Coy", None

    canonical, _ = COMPANIES.get(company.lower(), ("", ""))
    known = UNIT_COMPANIES.get(unit)
    if known is None:
        return canonical or smart_title(company), None  # 130SAR, 412SAR, Campops...

    if canonical in known:
        return canonical, None
    if company.lower() in HQ_ALIASES and unit in HQ_COMPANY:
        return HQ_COMPANY[unit], None

    return smart_title(company), ("WARN", f'company "{text}" is not a known {unit} company')


def resolve(raw_unit: str, raw_company: str) -> tuple:
    """Returns (unit, company, notes) from the two free-text answers, in either order."""
    notes = []
    u_unit, u_left = parse_field(raw_unit)
    c_unit, c_left = parse_field(raw_company)

    # Companies are unit-exclusive, so a known company name names its unit outright. This is
    # also what rescues a backwards 'SAR41', which no unit pattern matches.
    mapped = next((COMPANIES[k][1] for k in
                   (company_name(c_left).lower(), company_name(u_left).lower())
                   if k in COMPANIES), "")

    # The Unit field is taken at its word, including a bare number - '130' means 130SAR, even
    # when the company named belongs to another unit. Otherwise, most to least trustworthy.
    unit = next((u for u in (u_unit, mapped, c_unit) if u), "")

    if unit and unit != u_unit and not is_nil(raw_unit):
        notes.append(("INFO", f'unit "{raw_unit}" corrected to {unit} using company "{raw_company}"'))
    elif unit == u_unit and raw_unit.strip().isdigit():
        notes.append(("INFO", f'unit "{raw_unit}" read as {unit} - SAR suffix assumed'))

    if not unit:
        if raw_unit.strip().lower() in JUNK_UNITS:
            return "", "", notes + [("WARN", f'unit "{raw_unit}" names no unit - left blank')]
        unit = canon_unit(raw_unit)

    # The company usually sits in the Company field, but falls back to the Unit field when
    # the two were filled in the wrong boxes.
    company, note = canon_company(c_left or u_left, unit)
    if note:
        notes.append(note)
    return unit, company, notes


def dedupe(entries: list) -> tuple:
    """Keep one row per person: the latest entry that parsed cleanly, else the latest entry.

    entries are (key, order, valid, row) in file order; the survivor takes the position of
    the person's first row so the output stays in registration order.
    """
    counts = Counter(key for key, _, _, _ in entries)
    best = {}
    for key, order, valid, row in entries:
        if key not in best or (valid, order) > best[key][:2]:
            best[key] = (valid, order, row)

    notes = []
    seen = set()
    rows = []
    for key, _, _, _ in entries:
        if key in seen:
            continue
        seen.add(key)
        kept = best[key][2]
        if counts[key] > 1:
            notes.append(("INFO", kept[0], "registered more than once - kept the latest entry "
                                           "that parsed cleanly"))
        rows.append(kept)
    return rows, notes


def entry_order(timestamp: str, index: int):
    """FormSG's 'Response timestamp', falling back to file order if it can't be read."""
    try:
        return datetime.strptime(timestamp.strip(), "%d %b %Y %I:%M:%S %p")
    except ValueError:
        return datetime.min + timedelta(seconds=index)


def clean_service(raw: str) -> str:
    """'Option 1 NSF' -> 'NSF'."""
    return re.sub(r"^Option\s+\d+\s+", "", raw.strip())


def convert(in_path: Path, out_path: Path) -> tuple:
    """Read the export, write the cleaned roll, return (row_count, notes)."""
    with open(in_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    header = rows[HEADER_ROW]
    missing = [c for c in SOURCE_COLUMNS + ["Do you have a STRAVA account"] if c not in header]
    if missing:
        sys.exit(f"ERROR: {in_path.name} is missing expected column(s): {', '.join(missing)}")

    entries = []
    pending = {}   # notes per candidate row, reported only if that row survives dedupe
    for index, row in enumerate(rows[HEADER_ROW + 1:]):
        if not any(row):
            continue
        r = dict(zip(header, row))
        name = r["[Myinfo] Name"].strip()

        unit, company, row_notes = resolve(r["Unit"], r["Company"])

        strava = r["STRAVA User name"].strip()
        if is_nil(strava):
            strava = ""
            row_notes.append(("WARN", "no usable STRAVA username - will never match an activity"))

        out_row = [name, unit, company, clean_service(r["Type of service"]), strava]
        pending[id(out_row)] = [(level, name, message) for level, message in row_notes]
        entries.append((r.get("SingPass Validated NRIC", "").strip() or name.upper(),
                        entry_order(r.get("Response timestamp", ""), index),
                        not any(level == "WARN" for level, _ in row_notes),
                        out_row))

    out_rows, notes = dedupe(entries)
    for out_row in out_rows:
        notes.extend(pending[id(out_row)])

    # Match the existing roll's bytes: UTF-8 with BOM, LF endings, trailing newline.
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(OUTPUT_HEADER)
        writer.writerows(out_rows)

    return len(out_rows), notes


def write_b64(csv_path: Path, b64_path: Path) -> int:
    """Base64 the exact CSV bytes into a single-line ASCII file. Returns the char count."""
    encoded = base64.b64encode(csv_path.read_bytes()).decode("ascii")
    b64_path.write_text(encoded + "\n", encoding="ascii", newline="\n")
    return len(encoded)


def main():
    data_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="raw FormSG registration export CSV")
    args = parser.parse_args()

    out_path = data_dir / "nominal_roll.csv"
    b64_path = data_dir / "NOMINAL_ROLL_B64.txt"
    count, notes = convert(args.input, out_path)

    for level, name, message in sorted(notes):   # "INFO" sorts before "WARN"
        print(f"{level}: {name}: {message}", file=sys.stderr)

    flagged = sum(1 for lvl, _, _ in notes if lvl == "WARN")
    print(f"--- {count} rows written to {out_path}, {flagged} flagged ---", file=sys.stderr)

    chars = write_b64(out_path, b64_path)
    print(f"wrote {b64_path} ({chars} chars)", file=sys.stderr)
    print("REMINDER: paste its contents into the NOMINAL_ROLL_B64 GitHub secret - "
          "the dashboard reads that secret, not the CSV.", file=sys.stderr)


if __name__ == "__main__":
    main()
