"""Shared fixtures and import wiring for the ./test suite.

Run from the repo root with a Python that has the src deps installed
(pyyaml, requests, playwright, pytest):

    python -m pytest test/ -q

`src/` is a package; putting the repo root on sys.path is all that is needed
for `import src.<pkg>.<mod>` to resolve from anywhere under test/.

Every input in this suite is synthetic ("universal dummy") data. The real
src/nominal_roll/nominal_roll.csv and the real FormSG export are never read.
"""
import csv
import itertools
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.dashboard.names import NominalRoll  # noqa: E402  (after sys.path insert)

ROSTER_HEADER = ["Name", "Unit", "Company", "Type of service", "STRAVA username"]

# Five invented people: NSF + REGULAR (serving), Alumni + NSman (alumni),
# plus one more NSF. Cara has no company (SBW); Dave is 8SAB (no companies).
DUMMY_ROSTER = [
    ("ALICE ANON",   "40SAR", "Cougar", "NSF",     "Alice Anon"),
    ("BOB BOGUS",    "41SAR", "Falcon", "REGULAR", "Bob Bogus"),
    ("CARA CIPHER",  "SBW",   "",       "Alumni",  "Cara Cipher"),
    ("DAVE DUMMY",   "8SAB",  "",       "NSman",   "Dave Dummy"),
    ("ERIN EXAMPLE", "41SAR", "Hawk",   "NSF",     "Erin Example"),
]


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: exercises a real browser / network call; skipped unless opted in"
    )


def write_roster_csv(path, rows=DUMMY_ROSTER):
    """Write a nominal_roll.csv (UTF-8 BOM, matching production) at `path`."""
    with Path(path).open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(ROSTER_HEADER)
        w.writerows(rows)
    return Path(path)


@pytest.fixture
def roster_rows():
    return [dict(zip(ROSTER_HEADER, r)) for r in DUMMY_ROSTER]


@pytest.fixture
def roster_csv(tmp_path):
    return write_roster_csv(tmp_path / "nominal_roll.csv")


@pytest.fixture
def roll(roster_csv, monkeypatch):
    """A NominalRoll loaded from the dummy roster."""
    monkeypatch.setattr(NominalRoll, "CSV_PATH", roster_csv)
    return NominalRoll()


@pytest.fixture
def make_activity():
    """Factory for one activities.csv-shaped row dict, with sane defaults.

        make_activity("Alice Anon", distance_m=10_000, moving_time_s=3000)
    """
    seq = itertools.count(1)

    def _make(athlete_name="Alice Anon", *, athlete_id="1", distance_m=5000,
              moving_time_s=1800, elapsed_time_s=None, elev_gain_m=0,
              device_name="Garmin", start_date_utc="2026-09-14T07:00:00Z", **extra):
        i = next(seq)
        row = {
            "activity_id": f"act{i}",
            "athlete_id": str(athlete_id),
            "athlete_name": athlete_name,
            "start_date_utc": start_date_utc,
            "activity_name": "Run",
            "type": "Run",
            "distance_m": distance_m,
            "moving_time_s": moving_time_s,
            "elapsed_time_s": moving_time_s if elapsed_time_s is None else elapsed_time_s,
            "elev_gain_m": elev_gain_m,
            "device_name": device_name,
        }
        row.update(extra)
        return row

    return _make


@pytest.fixture
def dummy_members():
    """members.csv-shaped rows matching the dummy roster's STRAVA usernames.

    Cara's first_seen is later, so time-gated history tests can check she is
    absent from earlier days.
    """
    return [
        {"athlete_id": "1", "name": "Alice Anon", "first_seen": "2026-09-10T00:00:00+00:00"},
        {"athlete_id": "2", "name": "Bob Bogus", "first_seen": "2026-09-10T00:00:00+00:00"},
        {"athlete_id": "3", "name": "Cara Cipher", "first_seen": "2026-09-16T00:00:00+00:00"},
        {"athlete_id": "4", "name": "Dave Dummy", "first_seen": "2026-09-10T00:00:00+00:00"},
    ]


@pytest.fixture
def write_activities_csv():
    """Write activity-dict rows to a CSV `generate.load_activities` can read."""
    def _write(path, rows):
        fields = list({k: None for row in rows for k in row})
        with Path(path).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        return Path(path)
    return _write
