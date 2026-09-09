"""Integration test: the load -> group -> history chain in generate.py.

Wires load_activities / load_members / build_grouped_data / build_daily_history
together over temp CSVs and the dummy roll. Checks the rules that only show up
once the pieces are connected: activities before the challenge start (in local
time) are excluded, grouping follows the roll's Type of service, off-roll
runners land only in "all", and daily history is cumulative and gated by each
member's first_seen.
"""
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
        "athlete_id,name,first_seen,last_seen\n"
        "1,Alice Anon,2026-09-10T00:00:00+00:00,2026-09-18T00:00:00+00:00\n"
        "2,Gone Member,2026-09-10T00:00:00+00:00,2026-09-11T00:00:00+00:00\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(generate, "MEMBERS_CSV", p)
    assert len(generate.load_members()) == 2


def test_grouped_data_splits_serving_and_alumni(roll, make_activity, dummy_members):
    acts = [
        make_activity("Alice Anon", distance_m=10_000, moving_time_s=3000),                 # NSF
        make_activity("Cara Cipher", athlete_id="3", distance_m=4_000, moving_time_s=1200),  # Alumni
    ]
    groups = generate.build_grouped_data(acts, dummy_members, "14.9.2026", roll)
    assert groups["serving"]["run_count"] == 1
    assert groups["alumni"]["run_count"] == 1
    assert groups["all"]["run_count"] == 2


def test_off_roll_runner_counts_only_in_all(roll, make_activity, dummy_members):
    acts = [make_activity("Ghost Runner", athlete_id="99", distance_m=5_000, moving_time_s=1500)]
    members = dummy_members + [
        {"athlete_id": "99", "name": "Ghost Runner", "first_seen": "2026-09-10T00:00:00+00:00"}
    ]
    groups = generate.build_grouped_data(acts, members, "14.9.2026", roll)
    assert groups["all"]["run_count"] == 1
    assert groups["serving"]["run_count"] == 0 and groups["alumni"]["run_count"] == 0


def test_daily_history_is_cumulative_and_first_seen_gated(roll, make_activity, dummy_members):
    acts = [
        make_activity("Alice Anon", distance_m=10_000, moving_time_s=3000,
                      start_date_utc="2026-09-14T02:00:00Z"),
        make_activity("Cara Cipher", athlete_id="3", distance_m=6_000, moving_time_s=1800,
                      start_date_utc="2026-09-16T02:00:00Z"),
    ]
    hist = generate.build_daily_history(acts, dummy_members, roll, SGT)

    assert sorted(hist) == ["2026-09-14", "2026-09-16"]
    assert hist["2026-09-14"]["all"]["total_km"] == pytest.approx(10.0)
    assert hist["2026-09-16"]["all"]["total_km"] == pytest.approx(16.0)   # cumulative, not per-day
    # Cara's first_seen is 2026-09-16, so she is not in the day-14 member count
    assert hist["2026-09-14"]["all"]["athlete_count"] == 3
    assert hist["2026-09-16"]["all"]["athlete_count"] == 4
    # each historical bucket has been slimmed for the payload
    assert "zero_gap" in hist["2026-09-14"]["all"]
