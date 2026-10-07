"""Integration test for member_activities.run() and statistics.run() (no browser): profile weeks become ledger rows
(every sport) and foot-only weekly figures; statistics.csv gets today's cumulative rows on top of last week's
(leaderboard over profile, ledger fallback for the rest); earlier days survive; expiry is a ScrapeError and mass
failures or a 429 come back as a problem with what was found kept."""
import csv
from contextlib import contextmanager
from datetime import date, datetime

import pytest

from backend.activities import member_activities as A
from backend.statistics import statistics as S

TODAY = date(2026, 10, 1)


def _read(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _entry(aid, athlete_id, type_="Run", km="5.0", start="2026-09-29T00:00:00Z"):
    return {"entity": "Activity", "activity": {
        "id": aid, "athlete": {"athleteId": athlete_id}, "type": type_, "startDate": start,
        "stats": [{"key": "stat_one", "value": f"{km} km"}, {"key": "stat_one_subtitle", "value": "Distance"},
                  {"key": "stat_two", "value": "30m"}, {"key": "stat_two_subtitle", "value": "Time"}]}}


class _FakePage:
    """page.evaluate(BATCH_JS, {urls}) -> one canned result per this-week URL, keyed by athlete id; other weeks empty."""

    def __init__(self, by_athlete):
        self.by_athlete, self.urls = by_athlete, []

    def evaluate(self, script, arg):
        self.urls += arg["urls"]
        return [self.by_athlete[u.split("/")[2]] if "interval=202640&" in u else {"entries": []} for u in arg["urls"]]


@pytest.fixture
def env(tmp_path, monkeypatch):
    members = tmp_path / "members.csv"
    members.write_text("athlete_id,name,ingest_at,left_at\n1,Alice,2026-09-01,\n2,Bob,2026-09-01,\n"
                       "3,Cara,2026-09-01,\n4,Gone,2026-09-01,2026-09-20\n", encoding="utf-8")
    stats = tmp_path / "statistics.csv"   # last week's Sunday and an earlier day this week, both kept
    stats.write_text("athlete_id,date,distance_m,moving_time_s,elev_gain_m,activities,source,synced_at\n"
                     "1,2026-09-27,9000.0,3000,0,2,profile,2026-09-28T00:00:00+00:00\n"
                     "1,2026-09-29,14000.0,4800,0,3,profile,2026-09-30T00:00:00+00:00\n", encoding="utf-8")
    activities = tmp_path / "activities.csv"
    monkeypatch.setattr(A, "MEMBERS_CSV", members)
    monkeypatch.setattr(A, "CSV_PATH", activities)
    monkeypatch.setattr(S, "ACTIVITIES_CSV", activities)
    monkeypatch.setattr(S, "CSV_PATH", stats)

    class _Thursday(datetime):   # 2026-10-01: this week only, no Monday grace
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 1, 12, tzinfo=tz)
    monkeypatch.setattr(A, "datetime", _Thursday)
    monkeypatch.setattr(A, "now_utc", lambda: "2026-10-01T12:00:00+00:00")

    def use(page):
        @contextmanager
        def _club_page(*a):
            yield page
        monkeypatch.setattr(A, "club_page", _club_page)
    return tmp_path, use


def test_scan_ledgers_every_sport_and_statistics_take_foot_weeks_with_leaderboard_winning(env):
    tmp, use = env
    page = _FakePage({
        "1": {"entries": [_entry(10, 1), _entry(11, 1, "Walk", "1.5"), _entry(12, 1, "Ride", "30"),
                          _entry(13, 9)]},          # a ride, and a group-run partner's activity
        "2": {"entries": None},                       # private profile
        "3": {"entries": [_entry(30, 3)]},
    })
    use(page)

    weeks, synced_at, problem = A.run(last_scan="2026-09-30")
    assert problem is None
    assert synced_at == "2026-10-01T12:00:00+00:00"
    assert sorted(r["activity_id"] for r in _read(tmp / "activities.csv")) == ["10", "11", "12", "30"]
    assert {u.split("/")[2] for u in page.urls} == {"1", "2", "3"}                    # left members skipped
    assert {u.split("interval=")[1][:6] for u in page.urls} == {"202639", "202640"}   # last week and this week

    leaderboard = {"2026-09-28": {"3": {"name": "Cara", "distance_m": 8000.0, "moving_time_s": 2400,
                                        "elev_gain_m": 20.0, "activities": 2}}}
    S.run(weeks, leaderboard, synced_at, TODAY)
    rows = {(r["athlete_id"], r["date"]): r for r in _read(tmp / "statistics.csv")}

    assert rows[("1", "2026-09-27")]["distance_m"] == "9000.0"                       # last week kept
    assert rows[("1", "2026-09-29")]["distance_m"] == "14000.0"                      # earlier day kept
    assert rows[("1", "2026-10-01")] == {"athlete_id": "1", "date": "2026-10-01",   # last Sunday + run + walk
                                         "distance_m": "15500.0", "moving_time_s": "6600", "elev_gain_m": "0.0",
                                         "activities": "4", "source": "profile", "synced_at": synced_at}
    assert ("2", "2026-10-01") not in rows                                           # nothing visible -> no row
    assert rows[("3", "2026-10-01")]["source"] == "leaderboard"
    assert rows[("3", "2026-10-01")]["distance_m"] == "8000.0"


def test_fallback_adds_ledger_runs_the_last_row_does_not_cover(env):
    tmp, _ = env
    A.append_activities(A.normalise(_entry(20, 2)) + A.normalise(_entry(21, 2, "Ride")), "2026-09-30T05:00:00+00:00")
    A.append_activities(A.normalise(_entry(22, 1, start="2026-09-27T01:00:00Z")),   # late upload, before last row
                        "2026-10-01T05:00:00+00:00")
    A.append_activities(A.normalise(_entry(23, 1, start="2026-09-29T01:00:00Z")),   # already in the 29th's row
                        "2026-09-29T05:00:00+00:00")

    S.run({}, {}, "2026-10-01T06:00:00+00:00", TODAY)
    rows = {(r["athlete_id"], r["date"]): r for r in _read(tmp / "statistics.csv")}

    assert rows[("2", "2026-10-01")]["activities"] == "1"          # first row: the run, not the ride
    assert rows[("2", "2026-10-01")]["source"] == "feed"
    assert rows[("1", "2026-10-01")]["activities"] == "4"          # 3 + the late upload only
    assert rows[("1", "2026-10-01")]["distance_m"] == "19000.0"

    S.run({}, {}, "2026-10-01T07:00:00+00:00", TODAY)               # nothing new: same figures
    again = {(r["athlete_id"], r["date"]): r for r in _read(tmp / "statistics.csv")}
    assert again[("1", "2026-10-01")]["activities"] == "4"


def test_new_members_get_every_week_since_challenge_start(env):
    tmp, use = env
    (tmp / "members.csv").write_text("athlete_id,name,ingest_at,left_at\n1,Alice,2026-09-01,\n"
                                     "3,Cara,2026-10-01T03:00:00+00:00,\n", encoding="utf-8")
    page = _FakePage({"1": {"entries": []}, "3": {"entries": [_entry(30, 3)]}})
    use(page)
    A.run(last_scan="2026-09-30T15:00:00+00:00")
    weeks_of = lambda aid: sorted(u.split("interval=")[1][:6] for u in page.urls if u.split("/")[2] == aid)
    assert weeks_of("3") == ["202638", "202639", "202640"]
    assert weeks_of("1") == ["202639", "202640"]


def test_expired_session_is_a_scrape_error(env):
    _, use = env
    use(_FakePage({a: {"expired": True, "status": 401} for a in "123"}))
    with pytest.raises(A.ScrapeError, match="expired"):
        A.run(last_scan="")


def test_many_failed_requests_are_a_problem_but_keep_what_was_found(env):
    tmp, use = env
    use(_FakePage({"1": {"entries": [_entry(10, 1)]}, "2": {"error": "HTTP 500"}, "3": {"error": "HTTP 500"}}))
    _, _, problem = A.run(last_scan="2026-09-30")
    assert "2 of 6" in problem
    assert [r["activity_id"] for r in _read(tmp / "activities.csv")] == ["10"]


def test_a_429_stops_the_scan_and_keeps_what_was_fetched(env):
    tmp, use = env
    use(_FakePage({"1": {"entries": [_entry(10, 1)]}, "2": {"limited": True}, "3": None}))   # 3: never fetched
    weeks, _, problem = A.run(last_scan="2026-09-30")
    assert "429" in problem
    assert ("1", "2026-09-28") in weeks
    assert [r["scraped_at"] for r in _read(tmp / "activities.csv")] == ["2026-10-01T12:00:00+00:00"]   # = synced_at
