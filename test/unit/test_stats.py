"""Unit tests for the statistics / awards engine.

The rules under test: blank CSV numbers coerce to 0 (never NaN), speed only
counts for runs over 0.5 km, "real hill" runs need >= 5 km and >= 8 m+/km, the
leaderboard shows the whole unit (non-runners as zero rows), each award has a
qualifying threshold and is None when nobody clears it, and device stats put
real hardware above virtual platforms.
"""
import pytest

from src.dashboard.stats import (
    AthleteStats,
    ReportStats,
    _build_device_stats,
    _num,
    compute_stats,
)


@pytest.mark.parametrize("value,expected", [
    ("", 0.0), (None, 0.0), ("abc", 0.0), ("12.5", 12.5), (3, 3.0),
])
def test_num_coerces_blanks_to_zero(value, expected):
    assert _num(value) == expected


# --- AthleteStats.add_activity qualifiers ---------------------------------

def _act(dist_m, moving_s=1800, *, elev=0.0, elapsed=None):
    return {
        "distance_m": dist_m, "moving_time_s": moving_s,
        "elapsed_time_s": moving_s if elapsed is None else elapsed,
        "elev_gain_m": elev,
    }


def test_speed_ignores_runs_at_or_below_half_km():
    a = AthleteStats("X")
    a.add_activity(_act(400, 120))          # 0.4 km — too short to trust GPS speed
    assert a.avg_speed is None
    a.add_activity(_act(2000, 600))         # 2 km — counts
    assert a.avg_speed == pytest.approx(2000 / 600)


def test_longest_tracks_the_biggest_single_run():
    a = AthleteStats("X")
    for d in (3000, 9000, 1000):
        a.add_activity(_act(d))
    assert a.longest == pytest.approx(9.0)


def test_climber_bucket_needs_distance_and_gradient():
    a = AthleteStats("X")
    a.add_activity(_act(4900, elev=200))   # steep but < 5 km
    a.add_activity(_act(6000, elev=30))    # long enough but only 5 m+/km
    assert a.climber_run_km == 0
    a.add_activity(_act(6000, elev=60))    # 5 km+, 10 m+/km — qualifies
    assert a.climber_run_km == pytest.approx(6.0)
    assert a.climber_run_elev == pytest.approx(60)


def test_break_time_is_clamped_at_zero():
    a = AthleteStats("X")
    a.add_activity(_act(1000, 600, elapsed=900))   # +300 s stopped
    a.add_activity(_act(1000, 600, elapsed=500))   # elapsed < moving -> +0, not -100
    assert a.break_time == 300


# --- to_leaderboard_entry ----------------------------------------------------

def test_leaderboard_entry_gap_and_elev_per_km():
    a = AthleteStats("X")
    a.km = 10.0
    assert a.to_leaderboard_entry(10.0)["gap"] == "leader"
    assert a.to_leaderboard_entry(17.0)["gap"] == "–7.0"
    zero = AthleteStats("Y").to_leaderboard_entry(5.0)   # km == 0
    assert zero["elev_per_km"] is None                   # no ZeroDivision
    assert zero["avg_speed_ms"] == 0 and zero["avg_speed"] == "–"


@pytest.mark.parametrize("seconds,text", [(3600 + 5 * 60, "1h 5m"), (0, "0h 0m")])
def test_fmt_time(seconds, text):
    assert AthleteStats.fmt_time(seconds) == text


def test_spd_kmh():
    assert AthleteStats.spd_kmh(2.5) == "9.0 km/h"


# --- device stats --------------------------------------------------------

def test_device_stats_rank_hardware_above_virtual_and_drop_blanks():
    def ath(name, device):
        a = AthleteStats(name)
        a.devices = {device} if device else set()
        return a

    athletes = {}
    for i in range(3):
        athletes[f"g{i}"] = ath(f"g{i}", "Garmin")
    for i in range(2):
        athletes[f"z{i}"] = ath(f"z{i}", "Zwift")
    for i in range(5):
        athletes[f"s{i}"] = ath(f"s{i}", "Strava iPhone App")
    athletes["blank"] = ath("blank", "")

    rows = _build_device_stats(athletes)
    assert [r["device"] for r in rows] == ["Garmin", "Zwift", "Strava iPhone App"]
    counts = {r["device"]: r["count"] for r in rows}
    assert counts == {"Garmin": 3, "Zwift": 2, "Strava iPhone App": 5}


# --- compute_stats: totals, leaderboard, awards --------------------------

def test_totals_and_leaderboard_cover_the_whole_roster(make_activity, dummy_members, roll):
    acts = [
        make_activity("Alice Anon", distance_m=10_000, moving_time_s=3000),
        make_activity("Alice Anon", distance_m=5_000, moving_time_s=1500),
        make_activity("Bob Bogus", athlete_id="2", distance_m=8_000, moving_time_s=2400),
    ]
    s = compute_stats(acts, members=dummy_members, roll=roll).to_dict()

    assert s["run_count"] == 3
    assert s["athlete_count"] == 4               # every member row, runner or not
    assert s["total_km"] == pytest.approx(23.0)

    lb = s["leaderboard"]
    assert [r["name"] for r in lb[:2]] == ["ALICE ANON", "BOB BOGUS"]  # km-ranked, roster names
    assert lb[0]["gap"] == "leader" and lb[1]["gap"] == "–7.0"
    assert lb[0]["km"] == pytest.approx(15.0)
    assert lb[0]["unit"] == "40SAR" and lb[0]["company"] == "40SAR/Cougar"
    # members who never ran are appended as zero rows
    zero_names = {r["name"] for r in lb if r["acts"] == 0}
    assert zero_names == {"CARA CIPHER", "DAVE DUMMY"}


def test_blank_numbers_never_become_nan(make_activity, dummy_members, roll):
    acts = [make_activity("Alice Anon", distance_m="", moving_time_s="", elev_gain_m="")]
    s = compute_stats(acts, members=dummy_members, roll=roll).to_dict()
    assert s["total_km"] == 0.0 and s["total_elev"] == 0.0
    row = next(r for r in s["leaderboard"] if r["name"] == "ALICE ANON")
    assert row["avg_speed_ms"] == 0


def test_awards_are_none_when_nobody_ran(dummy_members, roll):
    s = compute_stats([], members=dummy_members, roll=roll)
    for award in ("king_km", "king_elev", "marathoner", "longest",
                  "fastest", "climber", "flatrunner"):
        assert getattr(s, award) is None
    assert s.run_count == 0 and s.athlete_count == 4
    assert len(s.leaderboard) == 4 and all(r["acts"] == 0 for r in s.leaderboard)


def test_fastest_ignores_sub_half_km_only_athletes(make_activity, dummy_members, roll):
    acts = [
        make_activity("Alice Anon", distance_m=400, moving_time_s=120),        # no speed
        make_activity("Bob Bogus", athlete_id="2", distance_m=5000, moving_time_s=1500),
    ]
    assert compute_stats(acts, members=dummy_members, roll=roll).fastest["name"] == "BOB BOGUS"


def test_climber_needs_thirty_hill_km(make_activity, dummy_members, roll):
    acts = [make_activity("Alice Anon", distance_m=6000, elev_gain_m=60, moving_time_s=1800)
            for _ in range(3)]                       # only 18 hill km
    assert compute_stats(acts, members=dummy_members, roll=roll).climber is None

    acts += [make_activity("Alice Anon", distance_m=6000, elev_gain_m=60, moving_time_s=1800)
             for _ in range(2)]                      # now 30 hill km, 10 m+/km
    assert compute_stats(acts, members=dummy_members, roll=roll).climber["name"] == "ALICE ANON"


def test_flatrunner_picks_min_gradient_over_fifty_km(make_activity, dummy_members, roll):
    acts = []
    for _ in range(6):   # Alice: 60 km, 10 m+/km
        acts.append(make_activity("Alice Anon", distance_m=10_000, elev_gain_m=100, moving_time_s=3000))
    for _ in range(5):   # Bob: 55 km, 2 m+/km  -> flattest qualifier
        acts.append(make_activity("Bob Bogus", athlete_id="2", distance_m=11_000, elev_gain_m=22, moving_time_s=3000))
    for _ in range(7):   # Erin: 49 km, 1 m+/km -> flatter but under the 50 km bar
        acts.append(make_activity("Erin Example", athlete_id="5", distance_m=7_000, elev_gain_m=7, moving_time_s=2100))
    s = compute_stats(acts, members=dummy_members, roll=roll)
    assert s.flatrunner["name"] == "BOB BOGUS"


def test_king_elev_value_uses_space_thousands_separator(make_activity, dummy_members, roll):
    acts = [make_activity("Alice Anon", distance_m=10_000, elev_gain_m=1234, moving_time_s=3000)]
    s = compute_stats(acts, members=dummy_members, roll=roll)
    assert s.king_elev == {"name": "ALICE ANON", "value": "1 234 m elevation"}


@pytest.mark.parametrize("break_s,expected", [
    (50, None),
    (200, {"name": "ALICE ANON", "value": "3 min of rest"}),
])
def test_fun_stats_break_king_threshold(make_activity, dummy_members, roll, break_s, expected):
    acts = [make_activity("Alice Anon", distance_m=5000, moving_time_s=1800,
                          elapsed_time_s=1800 + break_s)]
    assert compute_stats(acts, members=dummy_members, roll=roll).fun_stats["breaks"] == expected


def test_no_data_period_is_plain_report_stats():
    assert compute_stats([], None).to_dict() == ReportStats().to_dict()
