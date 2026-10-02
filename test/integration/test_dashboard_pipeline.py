"""Integration test: generate.py's load -> group -> history chain over temp CSVs and the dummy roll:
local-date start filter, service grouping, off-roll runners only in 'all', day-keyed cumulative history."""
from datetime import date
from zoneinfo import ZoneInfo

import pytest

from src.dashboard import generate

SGT = ZoneInfo("Asia/Singapore")


def test_load_activities_filters_by_local_challenge_start(
    tmp_path, monkeypatch, write_activities_csv, make_activity
):
    rows = [
        make_activity("Alice Anon", start_date_utc="2026-09-09T10:00:00Z"),   # before -> out
        make_activity("Alice Anon", start_date_utc="2026-09-20T10:00:00Z"),   # after  -> in
        make_activity("Bob Bogus", athlete_id="2",
                      start_date_utc="2026-09-13T20:00:00Z"),                  # 04:00 SGT 14th -> in
    ]
    p = write_activities_csv(tmp_path / "activities.csv", rows)
    monkeypatch.setattr(generate, "ACTIVITIES_CSV", p)

    kept = generate.load_activities("2026-09-14", SGT)
    assert sorted(r["_date"] for r in kept) == ["2026-09-14", "2026-09-20"]


def test_load_members_returns_every_row(tmp_path, monkeypatch):
    p = tmp_path / "members.csv"
    p.write_text(
        "athlete_id,name,first_seen\n"
        "1,Alice Anon,2026-09-10T00:00:00+00:00\n"
        "2,Gone Member,2026-09-10T00:00:00+00:00\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(generate, "MEMBERS_CSV", p)
    assert len(generate.load_members()) == 2


def test_grouped_data_splits_serving_and_alumni(roll, make_activity, dummy_members, weeks_from):
    acts = [
        make_activity("Alice Anon", distance_m=10_000, moving_time_s=3000),                 # NSF
        make_activity("Cara Cipher", athlete_id="3", distance_m=4_000, moving_time_s=1200),  # Alumni
    ]
    groups = generate.build_grouped_data(weeks_from(acts), acts, dummy_members, "14.9.2026", roll)
    assert groups["serving"]["run_count"] == 1
    assert groups["alumni"]["run_count"] == 1
    assert groups["all"]["run_count"] == 2


def test_off_roll_runner_counts_only_in_all(roll, make_activity, dummy_members, weeks_from):
    acts = [make_activity("Ghost Runner", athlete_id="99", distance_m=5_000, moving_time_s=1500)]
    members = dummy_members + [
        {"athlete_id": "99", "name": "Ghost Runner", "first_seen": "2026-09-10T00:00:00+00:00"}
    ]
    groups = generate.build_grouped_data(weeks_from(acts), acts, members, "14.9.2026", roll)
    assert groups["all"]["run_count"] == 1
    assert groups["serving"]["run_count"] == 0 and groups["alumni"]["run_count"] == 0


def test_daily_history_is_cumulative_snapshot_keyed_and_first_seen_gated(roll, make_activity, dummy_members,
                                                                         weeks_from):
    acts = [
        make_activity("Alice Anon", distance_m=10_000, moving_time_s=3000,
                      start_date_utc="2026-09-14T02:00:00Z", _date="2026-09-14"),
        make_activity("Cara Cipher", athlete_id="3", distance_m=6_000, moving_time_s=1800,
                      start_date_utc="2026-09-22T02:00:00Z", _date="2026-09-22"),
    ]
    hist = generate.build_daily_history(weeks_from(acts), [], acts, dummy_members, roll, date(2026, 9, 24))

    assert sorted(hist) == ["2026-09-14", "2026-09-22", "2026-09-24"]   # each snapshot date, then today
    assert hist["2026-09-14"]["all"]["total_km"] == pytest.approx(10.0)
    assert hist["2026-09-24"]["all"]["total_km"] == pytest.approx(16.0)   # cumulative, not per-week
    assert hist["2026-09-22"]["all"]["athlete_count"] == 4
    # Cara's first_seen is 2026-09-16, so a snapshot taken on the 15th doesn't count her yet
    early = generate.build_daily_history(weeks_from(acts[:1]), [], acts[:1], dummy_members, roll, date(2026, 9, 15))
    assert early["2026-09-15"]["all"]["athlete_count"] == 3


def _snap(day, km, synced_at=""):
    return {"athlete_id": "1", "date": day, "distance_m": str(km * 1000), "moving_time_s": "0",
            "elev_gain_m": "0", "activities": "1", "source": "profile", "synced_at": synced_at}


def test_latest_snapshot_per_athlete_replaces_earlier_days_and_carries_forward():
    snaps = [_snap("2026-09-27", 9), _snap("2026-09-29", 12), _snap("2026-10-01", 17)]
    km = lambda day: [float(r["distance_m"]) for r in generate.latest_by_athlete(snaps, day)]
    assert km("2026-09-26") == []
    assert km("2026-09-28") == [9000.0]     # no scan on the 28th: Sunday's total carries forward
    assert km("2026-10-01") == [17000.0]    # a later snapshot replaces, never adds


def test_daily_history_counts_feed_rows_from_their_date(roll, dummy_members):
    snaps = [_snap("2026-09-29", 3, "2026-09-29T15:00:00+00:00")]
    extras = [{"athlete_id": "1", "date": "2026-10-01", "distance_m": "2000",
               "moving_time_s": "0", "elev_gain_m": "0", "activities": 1, "source": "feed"}]
    hist = generate.build_daily_history(snaps, extras, [], dummy_members, roll, date(2026, 10, 1))
    assert hist["2026-09-29"]["all"]["total_km"] == pytest.approx(3.0)
    assert hist["2026-10-01"]["all"]["total_km"] == pytest.approx(5.0)


def test_feed_updates_add_only_runs_scraped_after_the_athletes_snapshot():
    snaps = [{"athlete_id": "1", "date": "2026-09-30", "distance_m": "5000",
              "synced_at": "2026-09-30T15:00:00+00:00"}]
    acts = [
        {"athlete_id": "1", "_date": "2026-09-29", "distance_m": "5000", "moving_time_s": "1800", "elev_gain_m": "0",
         "scraped_at": "2026-09-30T15:00:00+00:00"},   # found by that nightly scan: already in the snapshot
        {"athlete_id": "1", "_date": "2026-10-01", "distance_m": "3000", "moving_time_s": "900", "elev_gain_m": "0",
         "scraped_at": "2026-10-01T03:00:00+00:00"},   # hourly feed since -> added
        {"athlete_id": "2", "_date": "2026-09-29", "distance_m": "4000", "moving_time_s": "1200", "elev_gain_m": "0",
         "scraped_at": "2026-09-29T03:00:00+00:00"},   # private profile, no snapshot row -> feed is all we have
    ]
    extra = generate.feed_updates(snaps, acts)
    assert [(r["athlete_id"], r["date"], r["distance_m"]) for r in extra] == [
        ("1", "2026-10-01", "3000"), ("2", "2026-09-29", "4000")]


def test_load_daily_returns_every_row(tmp_path, monkeypatch):
    p = tmp_path / "daily.csv"
    p.write_text("athlete_id,date,distance_m,moving_time_s,elev_gain_m,activities,source\n"
                 "1,2026-09-20,5000,1800,10,1,profile\n", encoding="utf-8")
    monkeypatch.setattr(generate, "DAILY_CSV", p)
    assert generate.load_daily()[0]["date"] == "2026-09-20"


def test_daily_history_counts_a_late_seen_member_who_already_ran(roll, dummy_members, weeks_from, make_activity):
    """A leaderboard athlete first written to members.csv after their snapshot still gets their name in that
    day's history - daily rows carry no name of their own to fall back on."""
    acts = [make_activity("Cara Cipher", athlete_id="3", distance_m=6_000,   # Cara first_seen 2026-09-16
                          start_date_utc="2026-09-14T02:00:00Z", _date="2026-09-14")]
    hist = generate.build_daily_history(weeks_from(acts), [], [], dummy_members, roll, date(2026, 9, 15))
    names = {r["name"] for r in hist["2026-09-14"]["all"]["leaderboard"] if r["acts"]}
    assert names == {"CARA CIPHER"}
