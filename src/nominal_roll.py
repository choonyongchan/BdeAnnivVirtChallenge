"""Roster loading and athlete-name resolution, shared by generate.py and report_generator.py."""
import csv
from pathlib import Path

# Employers people typed into the Company field instead of their sub-unit.
JUNK_COMPANIES = {"fabrica robotics", "aia"}


class NominalRoll:
    """Maps Strava's (often truncated) display names to full formal names,
    and full names to their unit/company."""

    CSV_PATH = Path(__file__).parent.parent / "data" / "nominal_roll.csv"

    def __init__(self):
        self.name_map, self.unit_company_map = self._load(self.CSV_PATH)

    @staticmethod
    def _all_truncations(strava_name: str):
        """Yield every possible API-truncated form by splitting at each word boundary."""
        parts = strava_name.lower().split()
        yield strava_name.lower()  # ponytail: exact match first — handles full names returned by API
        if len(parts) < 2:
            return
        for i in range(1, len(parts)):
            prefix = " ".join(parts[:i])
            yield f"{prefix} {parts[i][0]}."

    def _load(self, path) -> tuple:
        """Returns (name_map, unit_company_map) from a single read of nominal_roll.csv.

        name_map: {truncated_strava_name: FULL_NAME}
        unit_company_map: {FULL_NAME: {unit, company, service}}, company
        unit-qualified ("40SAR/Cougar") and blank when the roll names none.
        """
        name_map = {}
        unit_company_map = {}
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    strava = row.get("STRAVA username", "").strip()
                    full = row.get("Name", "").strip()
                    if strava and full:
                        for key in self._all_truncations(strava):
                            name_map[key] = full
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
        return name_map, unit_company_map

    @staticmethod
    def full_name(person: dict) -> str:
        """Build a raw display name from a Strava athlete/member dict."""
        return f"{person.get('firstname', '')} {person.get('lastname', '')}".strip()

    def resolve(self, raw_name: str) -> str:
        """Map a raw Strava display name to its canonical roster name, if known."""
        return self.name_map.get(raw_name.lower().strip(), raw_name) if self.name_map else raw_name

    def unit_company(self, name: str) -> dict:
        return self.unit_company_map.get(name, {})

    def service(self, name: str) -> str:
        """Roll's "Type of service", upper-cased: NSF / REGULAR / NSMAN / ALUMNI."""
        return self.unit_company_map.get(name, {}).get("service", "")
