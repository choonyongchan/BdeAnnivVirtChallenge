"""Integration test: generate.py's load -> group -> history chain over temp CSVs and the dummy roll:
local-date start filter (foot sports only), service grouping, off-roll runners only in 'all', day-keyed cumulative
history with members gated by ingest_at/left_at and the headline count where recorded."""
from datetime import date
from zoneinfo import ZoneInfo

import pytest

from frontend import generate

SGT = ZoneInfo("Asia/Singapore")


def test_load_activities_filters_by_local_challenge_start(
    tmp_path, monkeypatch, write_activities_csv, make_activity
):
    rows = [
        make_activity("Alice Anon", start_date_utc="2026-09-09T10:00:00Z"),   # before -> out
        make_activity("Alice Anon", start_date_utc="2026-09-20T10:00:00Z"),   # after  -> in
        make_activity("Bob Bogus", athlete_id="2",
                      start_date_utc="2026-09-13T20:00:00Z"),                  # 04:00 SGT 14th -> in
        make_activity("Bob Bogus", athlete_id="2", type="Ride",
                      start_date_utc="2026-09-20T10:00:00Z"),                  # not a foot sport -> out
    ]
    p = write_activities_csv(tmp_path / "activities.csv", rows)
    monkeypatch.setattr(generate, "ACTIVITIES_CSV", p)

    kept = generate.load_activities("2026-09-14", SGT)
    assert sorted(r["_date"] for r in kept) == ["2026-09-14", "2026-09-20"]


def test_load_members_returns_every_row(tmp_path, monkeypatch):
    p = tmp_path / "members.csv"
    p.write_text(
        "athlete_id,name,ingest_at,left_at\n"
        "1,Alice Anon,2026-09-10T00:00:00+00:00,\n"
        "2,Gone Member,2026-09-10T00:00:00+00:00,2026-09-12T00:00:00+00:00\n",
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
        {"athlete_id": "99", "name": "Ghost Runner", "ingest_at": "2026-09-10T00:00:00+00:00"}
    ]
    groups = generate.build_grouped_data(weeks_from(acts), acts, members, "14.9.2026", roll)
    assert groups["all"]["run_count"] == 1
    assert groups["serving"]["run_count"] == 0 and groups["alumni"]["run_count"] == 0


def test_daily_history_is_cumulative_snapshot_keyed_and_ingest_gated(roll, make_activity, dummy_members,
                                                                         weeks_from):
    acts = [
        make_activity("Alice Anon", distance_m=10_000, moving_time_s=3000,
                      start_date_utc="2026-09-14T02:00:00Z", _date="2026-09-14"),
        make_activity("Cara Cipher", athlete_id="3", distance_m=6_000, moving_time_s=1800,
                      start_date_utc="2026-09-22T02:00:00Z", _date="2026-09-22"),
    ]
    hist = generate.build_daily_history(weeks_from(acts), acts, dummy_members, {}, roll, date(2026, 9, 24))

    assert sorted(hist) == ["2026-09-14", "2026-09-22", "2026-09-24"]   # each snapshot date, then today
    assert hist["2026-09-14"]["all"]["total_km"] == pytest.approx(10.0)
    assert hist["2026-09-24"]["all"]["total_km"] == pytest.approx(16.0)   # cumulative, not per-week
    assert hist["2026-09-22"]["all"]["athlete_count"] == 4
    # Cara's ingest_at is 2026-09-16, so a snapshot taken on the 15th doesn't count her yet
    early = generate.build_daily_history(weeks_from(acts[:1]), acts[:1], dummy_members, {}, roll, date(2026, 9, 15))
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


def test_members_count_from_ingest_until_they_leave():
    m = {"ingest_at": "2026-09-10T03:00:00+00:00", "left_at": "2026-09-20T03:00:00+00:00"}
    assert [generate.member_on(m, d) for d in ("2026-09-09", "2026-09-10", "2026-09-19", "2026-09-20")] == \
        [False, True, True, False]
    assert generate.member_on({"ingest_at": "2026-09-10", "left_at": ""}, "2026-12-31")


def test_headline_count_is_the_days_last_reading_carried_forward(tmp_path, monkeypatch):
    p = tmp_path / "member_count.csv"
    p.write_text("scraped_at,member_count\n2026-09-20T10:00:00+00:00,1001\n2026-09-20T16:30:00+00:00,1002\n"
                 "2026-09-22T01:00:00+00:00,1005\n", encoding="utf-8")   # 16:30Z is the 21st in Singapore
    monkeypatch.setattr(generate, "MEMBER_COUNT_CSV", p)
    counts = generate.load_member_counts(SGT)
    assert counts == {"2026-09-20": 1001, "2026-09-21": 1002, "2026-09-22": 1005}
    assert [generate.count_as_of(counts, d) for d in ("2026-09-19", "2026-09-21", "2026-09-30")] == [None, 1002, 1005]


def test_daily_history_takes_the_headline_where_recorded(roll, dummy_members, weeks_from, make_activity):
    acts = [make_activity("Alice Anon", start_date_utc="2026-09-14T02:00:00Z", _date="2026-09-14")]
    hist = generate.build_daily_history(weeks_from(acts), acts, dummy_members, {"2026-09-15": 1055}, roll,
                                        date(2026, 9, 16))
    assert hist["2026-09-14"]["all"]["athlete_count"] == 3        # before any headline: the roster
    assert hist["2026-09-16"]["all"]["athlete_count"] == 1055


def test_load_daily_returns_every_row(tmp_path, monkeypatch):
    p = tmp_path / "statistics.csv"
    p.write_text("athlete_id,date,distance_m,moving_time_s,elev_gain_m,activities,source\n"
                 "1,2026-09-20,5000,1800,10,1,profile\n", encoding="utf-8")
    monkeypatch.setattr(generate, "STATISTICS_CSV", p)
    assert generate.load_daily()[0]["date"] == "2026-09-20"


def test_daily_history_counts_a_late_seen_member_who_already_ran(roll, dummy_members, weeks_from, make_activity):
    """A leaderboard athlete first written to members.csv after their snapshot still gets their name in that
    day's history - daily rows carry no name of their own to fall back on."""
    acts = [make_activity("Cara Cipher", athlete_id="3", distance_m=6_000,   # Cara ingest_at 2026-09-16
                          start_date_utc="2026-09-14T02:00:00Z", _date="2026-09-14")]
    hist = generate.build_daily_history(weeks_from(acts), [], dummy_members, {}, roll, date(2026, 9, 15))
    names = {r["name"] for r in hist["2026-09-14"]["all"]["leaderboard"] if r["acts"]}
    assert names == {"CARA CIPHER"}
