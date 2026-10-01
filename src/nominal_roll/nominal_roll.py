"""Merge a FormSG registration export into nominal_roll.csv, then find its new STRAVA usernames on Strava.
    python -m src.nominal_roll.nominal_roll "<export.csv>" | --recheck
"""
# Discovery exists because members.py only grows the ledger from club-feed activity, so a
# member with no feed-visible run would otherwise never be backfilled.
import argparse
import csv
import difflib
import json
import random
import re
import sys
import threading
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..dashboard.names import FUZZY_THRESHOLD, _norm, _token_key
from ..members.members import CSV_PATH as MEMBERS_CSV, FIELDS as MEMBER_FIELDS
from ..strava_session import CLUB_ID, append_new_rows, club_page, csv_column_set, read_csv, require_auth

# Companies are unit-exclusive — that is what makes the company->unit backfill sound.
UNIT_COMPANIES = {
    "40SAR": ["Archer", "Braves", "Cougar", "Stallion", "Hercules"],  # Hercules = HQ coy
    "41SAR": ["Falcon", "Glory", "Hawk", "Shrike", "Heron"],          # Heron    = HQ coy
}
HQ_COMPANY = {"40SAR": "Hercules", "41SAR": "Heron"}
COMPANIES = {c.lower(): (c, u) for u, coys in UNIT_COMPANIES.items() for c in coys}

NIL_VALUES = {"", "-", "na", "n/a", "nil", "none"}
JUNK_UNITS = NIL_VALUES | {"singapore"}   # answers that name no unit at all
HQ_ALIASES = {"hq", "bn hq"}

# Units smart_title() alone cannot spell consistently. Keyed on lowercase alphanumerics.
UNIT_ALIASES = {"campops": "Campops"}

# The Unit dropdown's free-text escape hatch: 'Others: Nil', 'Others: Keat Hong camp 8sab'.
OTHERS_PREFIX = re.compile(r"^\s*others?\s*:\s*", re.IGNORECASE)

OUTPUT_HEADER = ["Name", "Unit", "Company", "Type of service", "STRAVA username"]

# The registrant's name: Myinfo-verified in the first exports, plain 'Name' since.
NAME_COLUMNS = ["[Myinfo] Name", "Name"]

# Source columns in the FormSG export, in output order.
SOURCE_COLUMNS = ["Unit", "Company", "Type of service", "STRAVA User name"]

HEADER_ROW = 5  # the export prefixes 5 metadata lines before the real header

# 'Response timestamp' has been exported in both of these.
TIMESTAMP_FORMATS = ("%d %b %Y %I:%M:%S %p", "%d/%m/%Y %H:%M")


def is_nil(value: str) -> bool:
    return value.strip().lower() in NIL_VALUES


def parse_field(text: str) -> tuple:
    """Split one free-text answer into (unit, text the unit match did not consume).
    People put unit and/or company text in either form field, so both fields go through this."""
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
    raw_unit = OTHERS_PREFIX.sub("", raw_unit)      # the dropdown wraps free text in 'Others: '
    raw_company = OTHERS_PREFIX.sub("", raw_company)
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
    """One row per person from (key, order, valid, row, row_notes) entries: latest clean, else latest.
    The survivor keeps the person's first position, so output stays in registration order."""
    counts = Counter(key for key, _, _, _, _ in entries)
    best = {}
    for key, order, valid, row, row_notes in entries:
        if key not in best or (valid, order) > best[key][:2]:
            best[key] = (valid, order, row, row_notes)

    notes = []
    seen = set()
    rows = []
    for key, _, _, _, _ in entries:
        if key in seen:
            continue
        seen.add(key)
        _, _, kept, kept_notes = best[key]
        if counts[key] > 1:
            notes.append(("INFO", kept[0], "registered more than once - kept the latest entry "
                                           "that parsed cleanly"))
        notes.extend(kept_notes)
        rows.append(kept)
    return rows, notes


def entry_order(timestamp: str, index: int):
    """FormSG's 'Response timestamp', falling back to file order if it can't be read."""
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(timestamp.strip(), fmt)
        except ValueError:
            pass
    return datetime.min + timedelta(seconds=index)


def clean_service(raw: str) -> str:
    """'Option 1 NSF' -> 'NSF'."""
    return re.sub(r"^Option\s+\d+\s+", "", raw.strip())


def row_fields(header: list, row: list) -> dict:
    """Map column name -> answer; when a name repeats, the first non-empty answer wins.
    The export carries two 'Unit'/'Company' pairs; an answer lands in one or the other."""
    fields = {}
    for key, value in zip(header, row):
        if not fields.get(key, "").strip():
            fields[key] = value
    return fields


def read_roll(path: Path) -> list:
    """The existing roll's data rows, or [] when there is no roll yet."""
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [row for row in list(csv.reader(f))[1:] if any(row)]


def merge(existing: list, new_rows: list) -> tuple:
    """Fold an export into the roll: re-registrations replace in place, new people append.
    Returns (rows, updated, added); the roll has no NRIC, so unlike dedupe() this keys on the name."""
    rows = list(existing)
    at = {row[0].strip().upper(): i for i, row in enumerate(rows)}
    updated = added = 0
    for row in new_rows:
        key = row[0].strip().upper()
        if key in at:
            rows[at[key]] = row
            updated += 1
        else:
            at[key] = len(rows)
            rows.append(row)
            added += 1
    return rows, updated, added


def read_export(in_path: Path) -> list:
    """The export's rows as dedupe() entries; exits if an expected column is missing."""
    with open(in_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    header = rows[HEADER_ROW]
    missing = [c for c in SOURCE_COLUMNS + ["Do you have a STRAVA account"] if c not in header]
    if not any(c in header for c in NAME_COLUMNS):
        missing.insert(0, " or ".join(NAME_COLUMNS))
    if missing:
        sys.exit(f"ERROR: {in_path.name} is missing expected column(s): {', '.join(missing)}")

    entries = []
    for index, row in enumerate(rows[HEADER_ROW + 1:]):
        if not any(row):
            continue
        r = row_fields(header, row)
        name = next(r[c] for c in NAME_COLUMNS if c in r).strip()

        unit, company, row_notes = resolve(r["Unit"], r["Company"])

        strava = r["STRAVA User name"].strip()
        if is_nil(strava):
            strava = ""
            row_notes.append(("WARN", "no usable STRAVA username - will never match an activity"))

        out_row = [name, unit, company, clean_service(r["Type of service"]), strava]
        entries.append((r.get("SingPass Validated NRIC", "").strip() or name.upper(),
                        entry_order(r.get("Response timestamp", ""), index),
                        not any(level == "WARN" for level, _ in row_notes),
                        out_row,
                        [(level, name, message) for level, message in row_notes]))
    return entries


def write_roll(out_path: Path, rows: list) -> None:
    """Write the roll byte-compatibly: UTF-8 with BOM, LF endings, trailing newline."""
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(OUTPUT_HEADER)
        writer.writerows(rows)


def convert(in_path: Path, out_path: Path) -> tuple:
    """Read the export, merge it into the roll at out_path, return (row_count, notes)."""
    out_rows, notes = dedupe(read_export(in_path))
    existing = read_roll(out_path)
    out_rows, updated, added = merge(existing, out_rows)
    if existing:
        notes.append(("INFO", "", f"merged into the existing roll: "
                                  f"{updated} updated, {added} added"))
    write_roll(out_path, out_rows)
    return len(out_rows), notes


STATE_PATH = Path(__file__).parent / "discover_state.json"   # gitignored: usernames + roll names
RECHECK_DAYS = 7
TOP_HITS = 6  # search results considered per username
WORKERS = 4   # each worker drives its own browser (Playwright's sync API is single-threaded)

# What each outcome means, and what happens next; printed per lookup and in the summary.
STATUS_MEANING = {
    "member": f"one of the top {TOP_HITS} hits matches and its profile lists the club -> added to members.csv",
    "not_in_club": "public profile found, but it does not list the club -> re-check in %d days" % RECHECK_DAYS,
    "private": "profile is private, so its clubs can't be read -> re-check in %d days" % RECHECK_DAYS,
    "name_mismatch": f"search found someone, but none of the top {TOP_HITS} names resembles the roll's username/Name -> "
                     "skipped (likely a different person) until the roll row changes",
    "no_result": "search returned no athlete at all -> skipped until the roll row changes",
}
SEARCH_URL = "https://www.strava.com/athletes/search?query="
PROFILE_URL = "https://www.strava.com/athletes/"

# Only the result list: the page header also links "My Profile" (/athletes/<you>).
HITS_JS = r"""() => {
    const seen = new Set(), out = [];
    for (const a of document.querySelectorAll('[class*="AthleteList"] a[href*="/athletes/"]')) {
        const m = a.href.match(/\/athletes\/(\d+)$/);
        if (m && a.innerText.trim() && !seen.has(m[1])) { seen.add(m[1]); out.push([m[1], a.innerText.trim()]); }
    }
    return out;
}"""
CLUBS_JS = "() => [...document.querySelectorAll('a[href*=\"/clubs/\"]')].map(a => a.href)"


def name_ok(display: str, roll_name: str, username: str) -> bool:
    """True when a Strava display name resembles the roll's username or real Name
    (equal, same words in any order, one a word-subset of the other, or fuzzy >= FUZZY_THRESHOLD)."""
    d = _norm(display)
    for ref in (username, roll_name):
        n = _norm(ref)
        if not n:
            continue
        if d == n or _token_key(display) == _token_key(ref):
            return True
        dt, nt = set(d.split()), set(n.split())
        if len(dt) >= 2 and len(nt) >= 2 and (dt <= nt or nt <= dt):
            return True
        if difflib.SequenceMatcher(None, d, n).ratio() >= FUZZY_THRESHOLD:
            return True
    return False


def is_due(entry: dict | None, roll_name: str, now: datetime) -> bool:
    """Whether a roll username needs (re)searching given what state remembers about it."""
    if entry is None:
        return True
    if entry["status"] in ("not_in_club", "private"):  # can flip without the roll changing
        return now - datetime.fromisoformat(entry["checked_at"]) >= timedelta(days=RECHECK_DAYS)
    if entry["status"] in ("no_result", "name_mismatch"):
        return entry.get("roll_name") != roll_name
    return False   # member: already in the ledger


def candidates(roll: list, member_names: set, state: dict, now: datetime) -> list:
    """Roll rows with a username that is not a ledger name and is new or due for a re-check."""
    out = []
    for r in roll:
        u = r["STRAVA username"].strip()
        if u and _norm(u) not in member_names and is_due(state.get(u), r["Name"], now):
            out.append(r)
    return out


def lookup(page, row: dict) -> dict:
    """One roll row -> {status, athlete_id?, display?} from the top TOP_HITS search results.
    A club member wins over a non-member; otherwise the first name match, else the top hit."""
    username = row["STRAVA username"].strip()
    page.goto(SEARCH_URL + urllib.parse.quote_plus(username), wait_until="networkidle")
    page.wait_for_timeout(1500)
    hits = page.evaluate(HITS_JS)[:TOP_HITS]
    if not hits:
        return {"status": "no_result"}
    results = []
    for athlete_id, display in hits:
        out = {"athlete_id": athlete_id, "display": display}
        if not name_ok(display, row["Name"], username):
            results.append({**out, "status": "name_mismatch"})
            continue
        page.goto(PROFILE_URL + athlete_id, wait_until="networkidle")
        page.wait_for_timeout(1200)
        if "This Account Is Private" in page.evaluate("() => document.body.innerText"):
            results.append({**out, "status": "private"})
            continue
        in_club = any(f"/clubs/{CLUB_ID}" in c for c in page.evaluate(CLUBS_JS))
        results.append({**out, "status": "member" if in_club else "not_in_club"})
        if in_club:
            break
    return next((r for r in results if r["status"] == "member"),
                next((r for r in results if r["status"] != "name_mismatch"), results[0]))


def _work(rows: list, progress: dict) -> list:
    """Look up `rows` in this worker's own browser; -> [(row, result)].
    A failed lookup is logged and dropped (retried next run) so it can't lose the rest."""
    out = []
    with club_page() as page:
        for row in rows:
            username = row["STRAVA username"].strip()
            try:
                res = lookup(page, row)
            except Exception as e:
                res, err = None, type(e).__name__
            with progress["lock"]:
                progress["done"] += 1
                tag = f"[{progress['done']}/{progress['total']}]"
                if res is None:
                    print(f"{tag} error         {username!r}: {err} -> will retry next run", flush=True)
                else:
                    hit = f", chose {res['display']!r} (id {res['athlete_id']})" if "display" in res else ""
                    print(f"{tag} {res['status']:<13} {username!r} (roll: {row['Name']}){hit}"
                          f" | {STATUS_MEANING[res['status']]}", flush=True)
            if res:
                out.append((row, res))
            page.wait_for_timeout(random.randint(800, 1800))
    return out


def _record(results: list, state: dict, stamp: str) -> list:
    """Remember each lookup outcome in `state`; -> members.csv rows for confirmed members not yet in it."""
    seen = csv_column_set(MEMBERS_CSV, "athlete_id")
    new = []
    for row, res in results:
        state[row["STRAVA username"].strip()] = {"status": res["status"], "roll_name": row["Name"], "checked_at": stamp}
        if res["status"] == "member" and res["athlete_id"] not in seen:
            seen.add(res["athlete_id"])
            new.append({"athlete_id": res["athlete_id"], "name": res["display"], "first_seen": stamp})
    return new


def discover(roll: list) -> list:
    """Search the due roll usernames in WORKERS browsers and append confirmed members to members.csv.
    Returns the athlete_ids of every confirmed club member, new to the ledger or not."""
    require_auth()
    now = datetime.now(timezone.utc)
    member_names = {_norm(r["name"]) for r in read_csv(MEMBERS_CSV)}
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    todo = candidates(roll, member_names, state, now)
    workers = max(1, min(WORKERS, len(todo)))
    print(f"Discover from the nominal roll: {len(todo)} usernames to search, {workers} in parallel "
          f"({len(state)} already remembered, {len(roll)} considered)", flush=True)

    progress = {"done": 0, "total": len(todo), "lock": threading.Lock()}
    results = []
    if todo:
        with ThreadPoolExecutor(workers) as pool:
            for chunk in pool.map(lambda i: _work(todo[i::workers], progress), range(workers)):
                results += chunk

    new = _record(results, state, now.isoformat(timespec="seconds"))
    append_new_rows(MEMBERS_CSV, MEMBER_FIELDS, new)
    STATE_PATH.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    counts = Counter(res["status"] for _, res in results)
    print(f"\n=== discover summary: {len(results)} of {len(todo)} looked up, {len(new)} new members -> {MEMBERS_CSV} ===")
    for status, meaning in STATUS_MEANING.items():
        print(f"  {status:<13} {counts[status]:>4}  {meaning}")
    return [res["athlete_id"] for _, res in results if res["status"] == "member"]


def trigger_rows(before: list, after: list) -> list:
    """Roll rows (as dicts) whose STRAVA username is new or changed by the merge (name-keyed, as merge())."""
    old = {row[0].strip().upper(): row[4].strip() for row in before}
    rows = (dict(zip(OUTPUT_HEADER, row)) for row in after)
    return [r for r in rows
            if r["STRAVA username"].strip() and old.get(r["Name"].strip().upper()) != r["STRAVA username"].strip()]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # athlete names may be non-ASCII
    roll_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path, nargs="?", help="raw FormSG registration export CSV")
    parser.add_argument("--no-discover", action="store_true", help="only merge; skip the Strava lookup and scan")
    parser.add_argument("--recheck", action="store_true",
                        help="no export: re-search roll usernames whose last lookup is due (not-in-club / private)")
    args = parser.parse_args()
    if bool(args.input) == args.recheck:
        parser.error("give an export CSV, or --recheck on its own")

    out_path = roll_dir / "nominal_roll.csv"
    if args.recheck:
        with out_path.open(encoding="utf-8-sig", newline="") as f:
            discover(list(csv.DictReader(f)))  # the daily backfill scans every member anyway
        return

    before = read_roll(out_path)
    count, notes = convert(args.input, out_path)

    for level, name, message in sorted(notes):   # "INFO" sorts before "WARN"
        print(f"{level}: {name}: {message}", file=sys.stderr)

    flagged = sum(1 for lvl, _, _ in notes if lvl == "WARN")
    print(f"--- {count} rows written to {out_path}, {flagged} flagged ---", file=sys.stderr)

    if not args.no_discover:
        ids = discover(trigger_rows(before, read_roll(out_path)))
        if ids:
            from ..activities.activities import ProfilesFeed
            ProfilesFeed(only=set(ids)).run()


if __name__ == "__main__":
    main()
