"""End-to-end: raw FormSG export -> convert() -> nominal_roll.csv -> dashboard.

Proves the pieces agree — the roll the converter writes is the roll the
dashboard groups by. Unit resolution and Type-of-service are independent: a
registrant whose unit could not be resolved still keeps their service group,
just with a blank unit on the leaderboard.
"""
import csv
from datetime import datetime
from zoneinfo import ZoneInfo

from src import config
from src.dashboard import generate
from src.dashboard.names import NominalRoll
from src.nominal_roll.parse_nominal_roll import convert

SGT = ZoneInfo("Asia/Singapore")

EXPORT_HEADER = [
    "Response timestamp", "[Myinfo] Name", "Type of service", "Unit", "Company",
    "Do you have a STRAVA account", "STRAVA User name", "SingPass Validated NRIC",
]
REGISTRANTS = [
    ["09 Sep 2026 09:00:00 AM", "Serving Sam", "Option 1 NSF", "41 SAR", "Hawk",
     "Yes", "Serving Sam", "S1"],
    ["09 Sep 2026 09:05:00 AM", "Alum Ann", "Option 4 ALUMNI", "40 SAR", "Cougar",
     "Yes", "Alum Ann", "S2"],
    ["09 Sep 2026 09:10:00 AM", "Junk Jim", "Option 1 NSF", "Singapore", "Nil",
     "Yes", "Junk Jim", "S3"],
]
RUNNERS = [("1", "Serving Sam"), ("2", "Alum Ann"), ("3", "Junk Jim")]


def _write_export(path):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for i in range(5):
            w.writerow([f"meta {i}"])
        w.writerow(EXPORT_HEADER)
        w.writerows(REGISTRANTS)
    return path


def _write_members(path):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["athlete_id", "name", "first_seen"])
        for aid, name in RUNNERS:
            w.writerow([aid, name, "2026-09-10T00:00:00+00:00"])
    return path


def _write_activities(path):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "activity_id", "athlete_id", "athlete_name", "start_date_utc",
            "distance_m", "moving_time_s", "elapsed_time_s", "elev_gain_m", "device_name",
        ])
        w.writeheader()
        for i, (aid, name) in enumerate(RUNNERS, 1):
            w.writerow({
                "activity_id": f"a{i}", "athlete_id": aid, "athlete_name": name,
                "start_date_utc": "2026-09-15T02:00:00Z", "distance_m": 6000,
                "moving_time_s": 1800, "elapsed_time_s": 1800, "elev_gain_m": 10,
                "device_name": "Garmin",
            })
    return path


def test_converted_roll_drives_dashboard_grouping(tmp_path, monkeypatch):
    roll_csv = tmp_path / "nominal_roll.csv"
    count, _ = convert(_write_export(tmp_path / "export.csv"), roll_csv)
    assert count == 3
    monkeypatch.setattr(NominalRoll, "CSV_PATH", roll_csv)
    monkeypatch.setattr(generate, "MEMBERS_CSV", _write_members(tmp_path / "members.csv"))
    monkeypatch.setattr(generate, "ACTIVITIES_CSV", _write_activities(tmp_path / "activities.csv"))

    gen = generate.DashboardGenerator(config.Config(challenge_start="2026-09-14"))
    gen.load()
    data, _ = gen.build(datetime(2026, 9, 20, 12, 0, tzinfo=gen.tzinfo))
    today = data["today"]

    serving = {r["name"] for r in today["serving"]["leaderboard"] if r["acts"]}
    alumni = {r["name"] for r in today["alumni"]["leaderboard"] if r["acts"]}

    assert "Serving Sam" in serving and "Serving Sam" not in alumni
    assert "Alum Ann" in alumni and "Alum Ann" not in serving
    assert today["all"]["run_count"] == 3

    by_name = {r["name"]: r for r in today["all"]["leaderboard"]}
    assert by_name["Serving Sam"]["unit"] == "41SAR"
    assert by_name["Serving Sam"]["company"] == "41SAR/Hawk"
    # Junk Jim: unit unresolvable -> blank, but his NSF service still groups him
    assert by_name["Junk Jim"]["unit"] == ""
    assert "Junk Jim" in serving
