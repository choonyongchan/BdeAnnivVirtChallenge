"""End-to-end: config + roster + scraped CSVs -> index.html via the real generate.run(),
every path on temp files and weather stubbed; checks the page and its invariants."""
import csv
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend import config
from frontend import generate
from frontend.names import NominalRoll

SGT = ZoneInfo("Asia/Singapore")
CHALLENGE_START = "2026-09-14"

ROSTER = [
    ("ALICE ANON",  "40SAR", "Cougar", "NSF",     "Alice Anon"),
    ("BOB BOGUS",   "41SAR", "Falcon", "REGULAR", "Bob Bogus"),
    ("CARA CIPHER", "SBW",   "",       "Alumni",  "Cara Cipher"),
    ("DAVE DUMMY",  "8SAB",  "",       "NSman",   "Dave Dummy"),
]

# athlete_id, name, ingest_at date
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
    ("2", "Bob Bogus",    "2026-09-22T03:00:00Z", 7000, 2100, 15),   # second Mon-Sun week
]
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=SGT)

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
        w.writerow(["athlete_id", "name", "ingest_at", "left_at"])
        for aid, name, seen in MEMBERS:
            w.writerow([aid, name, f"{seen}T00:00:00+00:00", ""])
    monkeypatch.setattr(generate, "MEMBERS_CSV", members)
    monkeypatch.setattr(generate, "MEMBER_COUNT_CSV", tmp_path / "member_count.csv")

    activities = tmp_path / "activities.csv"
    with activities.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "activity_id", "athlete_id", "athlete_name", "start_date_utc", "type",
            "distance_m", "moving_time_s", "elapsed_time_s", "elev_gain_m", "device_name",
        ])
        w.writeheader()
        for i, (aid, name, d, dist, mov, elev) in enumerate(ACTS, 1):
            w.writerow({
                "activity_id": f"a{i}", "athlete_id": aid, "athlete_name": name,
                "start_date_utc": d, "type": "Run", "distance_m": dist, "moving_time_s": mov,
                "elapsed_time_s": mov, "elev_gain_m": elev, "device_name": "Garmin",
            })
    monkeypatch.setattr(generate, "ACTIVITIES_CSV", activities)

    # Strava's figures for the same runs: each athlete's cumulative totals, snapshotted on the first Sunday and NOW
    totals = {}
    for day in ("2026-09-20", "2026-09-24"):
        for aid, _, d, dist, mov, elev in ACTS:
            if CHALLENGE_START <= d[:10] <= day:
                t = totals.setdefault((aid, day), [0, 0, 0, 0])
                for i, v in enumerate((dist, mov, elev, 1)):
                    t[i] += v
    daily = tmp_path / "statistics.csv"
    with daily.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["athlete_id", "date", "distance_m", "moving_time_s", "elev_gain_m", "activities", "source"])
        for (aid, day), t in totals.items():
            w.writerow([aid, day, *t, "profile"])
    monkeypatch.setattr(generate, "STATISTICS_CSV", daily)

    announce = tmp_path / "announce.md"
    announce.write_text("# Test Banner\nGo run.", encoding="utf-8")
    cfg_yaml = tmp_path / "config.yaml"
    cfg_yaml.write_text(
        "club:\n  name: Test Club\n  id: '1'\n"
        f"challenge_start: {CHALLENGE_START}\n"
        "timezone: Asia/Singapore\n"
        "schedule:\n  members: '*'\n  feed: '*'\n  leaderboard: '*'\n  member_scan: [23]\n"
        "weather:\n  latitude: 1.3835\n  longitude: 103.7478\n"
        f'announcement_path: "{str(announce).replace(chr(92), "/")}"\n'
        "browser:\n  channel: ''\n  headless: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_yaml)

    out = tmp_path / "index.html"
    monkeypatch.setattr(generate, "OUT_PATH", out)
    monkeypatch.setattr(generate, "USER_COUNT_PATH", tmp_path / "user-count.json")
    monkeypatch.setattr("frontend.weather.weather_html", lambda *a, **k: "")
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
    data, daily = generate.build(*generate.load(config.load()), NOW)
    today = data["today"]

    # the 2026-09-09 activity is before the challenge start -> 7 of 8 kept
    assert today["all"]["run_count"] == 7

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

    # history: one cumulative snapshot per Mon-Sun week's Sunday, today for the running week
    days = list(daily)
    assert days == ["2026-09-20", "2026-09-24"]
    kms = [daily[d]["all"]["total_km"] for d in days]
    assert kms == sorted(kms)


def test_headline_member_count_overrides_all_total(env, tmp_path):
    (tmp_path / "member_count.csv").write_text("scraped_at,member_count\n2026-09-23T04:00:00+00:00,1050\n"
                                               "2026-09-24T02:00:00+00:00,1055\n", encoding="utf-8")
    data, daily = generate.build(*generate.load(config.load()), NOW)

    assert data["today"]["all"]["athlete_count"] == 1055
    assert daily[max(daily)]["all"]["athlete_count"] == 1055
    assert daily[min(daily)]["all"]["athlete_count"] <= len(MEMBERS)   # before the first headline: roster-based

    generate.run()
    badge = json.loads((tmp_path / "user-count.json").read_text(encoding="utf-8"))
    assert badge["message"] == "1055"


@pytest.mark.parametrize("width", [390, 1440])
def test_built_page_runs_in_a_browser_without_errors(env, width):
    """The generated page in headless Chromium, offline: its script runs without errors, the totals render with
    the member count, and nothing overflows sideways at phone or desktop width."""
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    generate.run()
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:   # no bundled Chromium (python -m playwright install chromium)
            pytest.skip(f"Chromium unavailable: {e}")
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.route("http*://**", lambda route: route.abort())   # hermetic: no fonts, no network
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(env.as_uri())
        totals = page.inner_text("#totals")
        overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
        page.click("#btn-trend")   # another view renders too
        browser.close()
    assert errors == []
    assert f"{len(MEMBERS)}\nmembers" in totals
    assert overflow <= 0
