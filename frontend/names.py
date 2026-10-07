"""Resolve Strava display names to roll names (and so unit / company / service), strictly one-to-one.
Tiers: exact, word order, fuzzy, then the roll's real Name; a username several people declared matches nobody."""
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

#: Shortened given names people use on Strava, folded to the form the roll spells out.
NAME_ABBREVIATIONS = {"muhd": "muhammad", "md": "muhammad", "mohd": "muhammad",
                      "mhd": "muhammad", "mohamad": "muhammad",
                      "mohamed": "muhammad", "mohammad": "muhammad"}

#: Ranks people prefix to a Strava name ("2416 REC Pravin K") - they name no person.
RANK_TOKENS = {"rec", "recruit", "pte", "pfc", "lcp", "cpl", "cfc",
               "3sg", "2sg", "1sg", "ssg", "msg", "me1", "me2", "ct"}

#: Audit trail written by fit(): what matched non-exactly, and what did not.
REPORT_PATH = Path(__file__).parent.parent / "logs" / "name_matches.log"


def _norm(s: str) -> str:
    """Accent-stripped, lowercased, punctuation-flattened form: "Darren  Huang" -> "darren huang".
    Non-Latin scripts are kept - a few people registered a CJK username."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[\W_]+", " ", s).split())


def _token_key(s: str) -> str:
    """_norm with the words sorted, so surname-first/given-name-first both match."""
    return " ".join(sorted(_norm(s).split()))


def _name_tokens(s: str) -> frozenset:
    """The words of a name that identify a person: no serials ("1111"), no ranks."""
    return frozenset(NAME_ABBREVIATIONS.get(w, w) for w in _norm(s).split()
                     if not any(c.isdigit() for c in w) and w not in RANK_TOKENS)


def _covers(a, b) -> bool:
    """True when every word of a appears in b, a lone initial matching any word it begins."""
    return all(w in b or (len(w) == 1 and any(x.startswith(w) for x in b)) for w in a)


class NominalRoll:
    """Maps Strava display names to full formal names, and full names to unit/company."""

    #: The cleaned roster CSV, output of backend/nominal_roll/nominal_roll.py.
    CSV_PATH = Path(__file__).parent.parent / "backend" / "nominal_roll" / "nominal_roll.csv"

    def __init__(self):
        self._load(self.CSV_PATH)
        #: {raw Strava name: FULL_NAME}, filled by fit(). Empty until then.
        self.match_map = {}

    def _load(self, path) -> None:
        """Read the roll once, indexing usernames and flagging the ambiguous ones."""
        owners, token_owners = {}, {}
        #: [(raw_username, FULL_NAME)] - candidates for fuzzy matching.
        self.entries = []
        #: {FULL_NAME: {unit, company, service}}.
        self.unit_company_map = {}
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
                        self.entries.append((strava, full))
                    if full:
                        unit = row.get("Unit", "").strip()
                        company = row.get("Company", "").strip()
                        if company.lower() in JUNK_COMPANIES:
                            company = ""
                        self.unit_company_map[full] = {
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
        #: {key: [FULL_NAME, ...]} - declared by several people, so in neither lookup table.
        self.conflicts = {k: sorted(v) for k, v in merged.items() if len(v) > 1}
        #: {normalised username: FULL_NAME} and {sorted-word key: FULL_NAME}, unambiguous only.
        self.name_map = {k: next(iter(v)) for k, v in owners.items() if k not in self.conflicts}
        self.token_map = {k: next(iter(v)) for k, v in token_owners.items() if k not in self.conflicts}

    def fit(self, names) -> None:
        """Assign each raw Strava name at most one roster entry, one-to-one, tiers in confidence order.
        Names whose key is contested on the roll are skipped, as are the contesting entries."""
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

        # Exact / order tiers: the username as declared, cosmetics and word order aside.
        for tier, key, table in (("exact", _norm, self.name_map),
                                 ("order", _token_key, self.token_map)):
            for n in pending():
                full = table.get(key(n))
                if full and full not in blocked:
                    claim(n, full, tier, 1.0, "")

        # Fuzzy tier: score every surviving pair, settle best-first so the strongest wins.
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

        ambiguous = self._fit_real_names(names, claim)
        self._write_report(names, matched, ambiguous)

    def _fit_real_names(self, names, claim) -> list:
        """Real-name tier: match the roll's Name column when the username was blank or wrong.
        Returns the names refused because several roster entries fit equally well."""
        # Roster entries still free. Conflicted ones are eligible: a contested username says
        # nothing about whose real name is whose, so this tier is independent evidence.
        taken = set(self.match_map.values())
        pool = [(f, _name_tokens(f)) for f in self.unit_company_map if f not in taken]
        ambiguous = []
        for n in sorted(n for n in names if n not in self.match_map):
            # Two tokens minimum, because one word names too many people to be evidence.
            tn = _name_tokens(n)
            if len(tn) < 2:
                continue
            # Containment either way: a Strava name may be richer or poorer than the roster name.
            cands = [f for f, tf in pool
                     if _covers(tn, tf) or (len(tf) >= 2 and _covers(tf, tn))]
            # One candidate or none, because a wrong claim credits one person's runs to another.
            if len(cands) == 1:
                claim(n, cands[0], "real", 1.0, cands[0])
            elif cands:
                ambiguous.append((n, cands))
        return ambiguous

    def _write_report(self, names, matched, ambiguous) -> None:
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
        lines += ["", "== ambiguous real-name candidates (matched to nobody) =="]
        lines += [f"  {name!r} -> {' | '.join(sorted(cands))}"
                  for name, cands in sorted(ambiguous)] or ["  (none)"]
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
