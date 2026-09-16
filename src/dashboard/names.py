"""Roster loading and athlete-name resolution.

Maps Strava display names to full formal names, and full names to their unit /
company / type of service.

The username people self-report on the registration form rarely matches their
Strava display name character for character, so the match runs in tiers --
normalised exact, then word-order-insensitive, then fuzzy -- and is fitted once
over every known athlete name so the result is strictly one-to-one: no two
Strava accounts resolve to the same person, and no account claims two. A
username that several people on the roll declared is ambiguous and matches
nobody; fit() reports those so the roll itself can be corrected.
"""
import csv
import difflib
import re
import unicodedata
from pathlib import Path

# Employers people typed into the Company field instead of their sub-unit.
JUNK_COMPANIES = {"fabrica robotics", "aia"}

#: Minimum SequenceMatcher ratio for a fuzzy match. Tuned against the real roll:
#: 0.90 accepts every correct near-miss and still rejects "Darren Ho" against
#: "Warren Ho" (0.89), who are two different people.
FUZZY_THRESHOLD = 0.90

#: Audit trail written by fit(): what matched non-exactly, and what did not.
REPORT_PATH = Path(__file__).parent.parent.parent / "logs" / "name_matches.log"


def _norm(s: str) -> str:
    """Accent-stripped, lowercased, punctuation-flattened form used for comparison.

    Collapses the cosmetic differences that make a self-reported username miss:
    "Darren  Huang" and "Marcus ." normalise onto "darren huang" and "marcus".
    Non-Latin scripts are kept as-is - a few people registered a CJK username,
    and stripping to ASCII would leave nothing to match on.
    """
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[\W_]+", " ", s).split())


def _token_key(s: str) -> str:
    """_norm with the words sorted, so surname-first/given-name-first both match."""
    return " ".join(sorted(_norm(s).split()))


class NominalRoll:
    """Maps Strava display names to full formal names, and full names to unit/company."""

    #: The cleaned roster CSV, output of src/nominal_roll/nominal_roll.py.
    CSV_PATH = Path(__file__).parent.parent / "nominal_roll" / "nominal_roll.csv"

    def __init__(self):
        # name_map:  {normalised_username: FULL_NAME}  - unambiguous entries only.
        # token_map: {sorted-word key: FULL_NAME}      - unambiguous entries only.
        # conflicts: {key: [FULL_NAME, ...]}           - declared by several people.
        # entries:   [(raw_username, FULL_NAME)]       - candidates for fuzzy matching.
        # unit_company_map: {FULL_NAME: {unit, company, service}}.
        (self.name_map, self.token_map, self.conflicts,
         self.entries, self.unit_company_map) = self._load(self.CSV_PATH)
        #: {raw Strava name: FULL_NAME}, filled by fit(). Empty until then.
        self.match_map = {}

    def _load(self, path) -> tuple:
        """Read the roll once, indexing usernames and flagging the ambiguous ones.

        A key claimed by two or more different people cannot be resolved by any
        rule, so it is recorded in conflicts and kept out of every lookup table.
        """
        owners, token_owners = {}, {}
        entries = []
        unit_company_map = {}
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    strava = row.get("STRAVA username", "").strip()
                    full = row.get("Name", "").strip()
                    # A username of pure punctuation (".", "。。") normalises to
                    # nothing and carries no signal - treat it as not given.
                    if strava and full and _norm(strava):
                        owners.setdefault(_norm(strava), set()).add(full)
                        token_owners.setdefault(_token_key(strava), set()).add(full)
                        entries.append((strava, full))
                    if full:
                        unit = row.get("Unit", "").strip()
                        company = row.get("Company", "").strip()
                        if company.lower() in JUNK_COMPANIES:
                            company = ""
                        unit_company_map[full] = {
                            "unit":    unit,
                            "company": f"{unit}/{company}" if unit and company else company,
                            "service": row.get("Type of service", "").strip().upper(),
                        }
        except FileNotFoundError:
            pass

        # One key can be reached as a plain form and as a sorted-word form; union
        # them so a clash either way counts as a clash.
        merged = {}
        for table in (owners, token_owners):
            for key, names in table.items():
                merged.setdefault(key, set()).update(names)
        conflicts = {k: sorted(v) for k, v in merged.items() if len(v) > 1}

        name_map = {k: next(iter(v)) for k, v in owners.items() if k not in conflicts}
        token_map = {k: next(iter(v)) for k, v in token_owners.items() if k not in conflicts}
        return name_map, token_map, conflicts, entries, unit_company_map

    def fit(self, names) -> None:
        """Assign each raw Strava name at most one roster entry, one-to-one.

        Works through the tiers in confidence order; a roster entry taken by an
        earlier tier is never offered again, and within the fuzzy tier the best
        scoring pairs are settled first. Names whose own key is contested on the
        roll are skipped outright - guessing there would credit one person's runs
        to another - as are the roster entries doing the contesting.
        """
        names = sorted({(n or "").strip() for n in names} - {""})
        blocked = {f for v in self.conflicts.values() for f in v}
        self.match_map = {}
        taken = set()
        matched = []

        def claim(name, full, tier, score, via):
            if name in self.match_map or full in taken:
                return
            self.match_map[name] = full
            taken.add(full)
            matched.append((tier, score, name, via, full))

        def pending():
            """Names still unmatched and not tangled up in a roll conflict."""
            return [n for n in names
                    if n not in self.match_map
                    and _norm(n) not in self.conflicts
                    and _token_key(n) not in self.conflicts]

        for tier, key, table in (("exact", _norm, self.name_map),
                                 ("order", _token_key, self.token_map)):
            for n in pending():
                full = table.get(key(n))
                if full and full not in blocked:
                    claim(n, full, tier, 1.0, "")

        # Fuzzy tier: score every surviving pair, then settle best-first so the
        # strongest match gets first refusal on a roster entry.
        pool = [(u, f) for u, f in self.entries if f not in taken and f not in blocked]
        pairs = []
        for n in pending():
            for u, f in pool:
                score = max(
                    difflib.SequenceMatcher(None, _norm(n), _norm(u)).ratio(),
                    difflib.SequenceMatcher(None, _token_key(n), _token_key(u)).ratio(),
                )
                if score >= FUZZY_THRESHOLD:
                    pairs.append((score, n, f, u))
        for score, n, f, u in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
            claim(n, f, "fuzzy", score, u)

        self._write_report(names, matched)

    def _write_report(self, names, matched) -> None:
        """Write the fit() audit trail, so a wrong match is visible rather than silent."""
        unmatched = [n for n in names if n not in self.match_map]
        lines = [
            f"{len(self.match_map)} of {len(names)} names matched, "
            f"{len(unmatched)} unmatched, {len(self.conflicts)} ambiguous roll usernames.",
            "",
            "== non-exact matches ==",
        ]
        lines += [f"  {tier:<5} {score:.3f}  {name}  ~  {via or name}  ->  {full}"
                  for tier, score, name, via, full in matched if tier != "exact"] or ["  (none)"]
        lines += ["", "== ambiguous roll usernames (matched to nobody) =="]
        lines += [f"  {key!r} declared by: {', '.join(owners)}"
                  for key, owners in sorted(self.conflicts.items())] or ["  (none)"]
        lines += ["", "== unmatched names =="]
        lines += [f"  {n}" for n in unmatched] or ["  (none)"]
        try:
            REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass  # the report is an aid, never a reason to fail the run

    def resolve(self, raw_name: str) -> str:
        """Map a raw Strava display name to its canonical roster name, if known."""
        raw_name = (raw_name or "").strip()
        if raw_name in self.match_map:
            return self.match_map[raw_name]
        return self.name_map.get(_norm(raw_name), raw_name)

    def unit_company(self, name: str) -> dict:
        """Roll's {unit, company, service} for a full name, or {} if absent."""
        return self.unit_company_map.get(name, {})

    def service(self, name: str) -> str:
        """Roll's "Type of service", upper-cased: NSF / REGULAR / NSMAN / ALUMNI."""
        return self.unit_company_map.get(name, {}).get("service", "")
