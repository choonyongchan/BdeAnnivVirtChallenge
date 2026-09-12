"""End-to-end: config + roster + scraped CSVs -> index.html.

Drives the real `generate.run()` with every path pointed at temp files and the
weather call stubbed, then checks the output page and the reasoning behind it:
placeholders are all filled, pre-challenge activities never count, the
serving/alumni groups are subsets of "all", the leaderboard shows the whole
roster, member count is independent of who ran, and daily history is sorted and
cumulative.
"""
import csv
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src import config
from src.dashboard import generate
from src.dashboard.names import NominalRoll

SGT = ZoneInfo("Asia/Singapore")
CHALLENGE_START = "2026-09-14"

ROSTER = [
    ("ALICE ANON",  "40SAR", "Cougar", "NSF",     "Alice Anon"),
    ("BOB BOGUS",   "41SAR", "Falcon", "REGULAR", "Bob Bogus"),
    ("CARA CIPHER", "SBW",   "",       "Alumni",  "Cara Cipher"),
    ("DAVE DUMMY",  "8SAB",  "",       "NSman",   "Dave Dummy"),
]

# athlete_id, name, first_seen date
MEMBERS = [
    ("1", "Alice Anon", "2026-09-10"),
    ("2", "Bob Bogus", "2026-09-10"),
    ("3", "Cara Cipher", "2026-09-10"),
    ("4", "Dave Dummy", "2026-09-10"),
    ("99", "Ghost Runner", "2026-09-10"),          # runs, but not on the roll
]

# athlete_id, name, start_date_utc, distance_m, moving_s, elev_m
ACTS = [
    ("1", "Alice Anon",   "2026-09-09T09:00:00Z", 9000, 2700, 20),   # before start -> excluded
    ("1", "Alice Anon",   "2026-09-14T02:00:00Z", 10000, 3000, 30),
    ("1", "Alice Anon",   "2026-09-16T02:00:00Z", 12000, 3600, 900),
    ("2", "Bob Bogus",    "2026-09-14T03:00:00Z", 8000, 2400, 10),
    ("2", "Bob Bogus",    "2026-09-18T03:00:00Z", 300, 90, 0),
    ("3", "Cara Cipher",  "2026-09-16T04:00:00Z", 5000, 1500, 5),
    ("99", "Ghost Runner", "2026-09-17T04:00:00Z", 6000, 1800, 5),
]

PLACEHOLDERS = ("__DATA__", "__DAILY_DATA__", "__UPDATED_HUMAN__", "__WEATHER__",
                "__ANNOUNCEMENT__", "__CLUB_NAME__", "__CLUB_SHORT__", "__CLUB_ID__")


@pytest.fixture
def env(tmp_path, monkeypatch):
    roster = tmp_path / "nominal_roll.csv"
    with roster.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Unit", "Company", "Type of service", "STRAVA username"])
        w.writerows(ROSTER)
    monkeypatch.setattr(NominalRoll, "CSV_PATH", roster)

    members = tmp_path / "members.csv"
    with members.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["athlete_id", "name", "first_seen"])
        for aid, name, seen in MEMBERS:
            w.writerow([aid, name, f"{seen}T00:00:00+00:00"])
    monkeypatch.setattr(generate, "MEMBERS_CSV", members)

    activities = tmp_path / "activities.csv"
    with activities.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "activity_id", "athlete_id", "athlete_name", "start_date_utc",
            "distance_m", "moving_time_s", "elapsed_time_s", "elev_gain_m", "device_name",
        ])
        w.writeheader()
        for i, (aid, name, d, dist, mov, elev) in enumerate(ACTS, 1):
            w.writerow({
                "activity_id": f"a{i}", "athlete_id": aid, "athlete_name": name,
                "start_date_utc": d, "distance_m": dist, "moving_time_s": mov,
                "elapsed_time_s": mov, "elev_gain_m": elev, "device_name": "Garmin",
            })
    monkeypatch.setattr(generate, "ACTIVITIES_CSV", activities)

    announce = tmp_path / "announce.md"
    announce.write_text("# Test Banner\nGo run.", encoding="utf-8")
    cfg_yaml = tmp_path / "config.yaml"
    cfg_yaml.write_text(
        "club:\n  name: Test Club\n  id: '1'\n"
        f"challenge_start: {CHALLENGE_START}\n"
        "timezone: Asia/Singapore\n"
        "weather:\n  latitude: 1.3835\n  longitude: 103.7478\n"
        f'announcement_path: "{str(announce).replace(chr(92), "/")}"\n'
        "browser:\n  channel: ''\n  headless: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_yaml)

    out = tmp_path / "index.html"
    monkeypatch.setattr(generate, "OUT_PATH", out)
    monkeypatch.setattr("src.dashboard.weather.weather_html", lambda *a, **k: "")
    return out


def test_run_writes_a_fully_filled_page(env):
    generate.run()
    html = env.read_text(encoding="utf-8")

    assert env.stat().st_size > 10_000
    for token in PLACEHOLDERS:
        assert token not in html
    assert '"leaderboard"' in html and '"label"' in html    # data blob embedded
    assert "Test Banner" in html                            # announcement wired through


def test_rationale_invariants_hold(env):
    gen = generate.DashboardGenerator(generate.load_config())
    gen.load()
    data, daily = gen.build(datetime(2026, 9, 20, 12, 0, tzinfo=gen.tzinfo))
    today = data["today"]

    # the 2026-09-09 activity is before the challenge start -> 6 of 7 kept
    assert today["all"]["run_count"] == 6

    # serving + alumni never exceed all, and their runners are a subset of all's
    assert today["serving"]["run_count"] + today["alumni"]["run_count"] <= today["all"]["run_count"]
    all_runners = {r["name"] for r in today["all"]["leaderboard"] if r["acts"]}
    for group in ("serving", "alumni"):
        assert {r["name"] for r in today[group]["leaderboard"] if r["acts"]} <= all_runners

    # the leaderboard carries the whole roster, runners or not
    lb_names = {r["name"] for r in today["all"]["leaderboard"]}
    assert {"ALICE ANON", "BOB BOGUS", "CARA CIPHER", "DAVE DUMMY"} <= lb_names

    # member count is every members.csv row, regardless of who ran
    assert today["all"]["athlete_count"] == len(MEMBERS)

    # daily history: keys sorted, first day is the challenge start, km cumulative
    days = list(daily)
    assert days == sorted(days) and days[0] == CHALLENGE_START
    kms = [daily[d]["all"]["total_km"] for d in days]
    assert kms == sorted(kms)
