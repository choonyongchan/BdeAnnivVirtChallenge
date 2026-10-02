"""Integration test for member_activities.run() (no browser): profile weeks become today's cumulative daily.csv rows
(own foot activities only, on top of last week's row) and ledger rows; the leaderboard overrides; earlier days
survive; expiry and mass failures are ScrapeErrors."""
import csv
from contextlib import contextmanager
from datetime import datetime

import pytest

from src.activities import member_activities as A
from src.activities import member_statistics as S


def _read(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _entry(aid, athlete_id, type_="Run", km="5.0"):
    return {"entity": "Activity", "activity": {
        "id": aid, "athlete": {"athleteId": athlete_id}, "type": type_, "startDate": "2026-09-29T00:00:00Z",
        "stats": [{"key": "stat_one", "value": f"{km} km"}, {"key": "stat_one_subtitle", "value": "Distance"},
                  {"key": "stat_two", "value": "30m"}, {"key": "stat_two_subtitle", "value": "Time"}]}}


class _FakePage:
    """page.evaluate(BATCH_JS, {urls}) -> one canned result per URL, keyed by athlete id."""

    def __init__(self, by_athlete):
        self.by_athlete, self.urls = by_athlete, []

    def evaluate(self, script, arg):
        self.urls += arg["urls"]
        return [self.by_athlete[u.split("/")[2]] for u in arg["urls"]]


@pytest.fixture
def env(tmp_path, monkeypatch):
    members = tmp_path / "members.csv"
    members.write_text("athlete_id,name,first_seen\n1,Alice,x\n2,Bob,x\n3,Cara,x\n", encoding="utf-8")
    daily = tmp_path / "daily.csv"   # last week's Sunday and an earlier day this week, both kept
    daily.write_text("athlete_id,date,distance_m,moving_time_s,elev_gain_m,activities,source\n"
                     "1,2026-09-27,9000.0,3000,0,2,profile\n"
                     "1,2026-09-29,14000.0,4800,0,3,profile\n", encoding="utf-8")
    for name, path in (("MEMBERS_CSV", members), ("CSV_PATH", tmp_path / "activities.csv")):
        monkeypatch.setattr(A, name, path)
    monkeypatch.setattr(S, "DAILY_CSV", daily)
    monkeypatch.setattr(A, "require_auth", lambda: None)

    class _Thursday(datetime):   # 2026-10-01: this week only, no Monday grace
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 1, 12, tzinfo=tz)
    monkeypatch.setattr(A, "datetime", _Thursday)

    def use(page):
        @contextmanager
        def _club_page(*a):
            yield page
        monkeypatch.setattr(A, "club_page", _club_page)
    return tmp_path, use


def test_sync_writes_profile_weeks_ledger_and_leaderboard_wins(env):
    tmp, use = env
    page = _FakePage({
        "1": {"entries": [_entry(10, 1), _entry(11, 1, "Walk", "1.5"), _entry(12, 1, "Ride", "30"),
                          _entry(13, 9)]},          # a ride, and a group-run partner's activity
        "2": {"entries": None},                       # private profile
        "3": {"entries": [_entry(30, 3)]},
    })
    use(page)
    leaderboard = {"2026-09-28": {"3": {"name": "Cara", "distance_m": 8000.0, "moving_time_s": 2400,
                                        "elev_gain_m": 20.0, "activities": 2}}}

    assert A.run(leaderboard) == 3           # ledger: Alice's run + walk, Cara's run
    rows = {(r["athlete_id"], r["date"]): r for r in _read(tmp / "daily.csv")}

    assert all("interval=202640&interval_type=week" in u for u in page.urls)
    assert rows[("1", "2026-09-27")]["distance_m"] == "9000.0"                       # last week kept
    assert rows[("1", "2026-09-29")]["distance_m"] == "14000.0"                      # earlier day kept
    assert rows[("1", "2026-10-01")] == {"athlete_id": "1", "date": "2026-10-01",   # last Sunday + this week
                                         "distance_m": "15500.0", "moving_time_s": "6600", "elev_gain_m": "0.0",
                                         "activities": "4", "source": "profile",
                                         "synced_at": "2026-10-01T12:00:00+00:00"}
    assert ("2", "2026-10-01") not in rows                                           # nothing visible -> no row
    assert rows[("3", "2026-10-01")]["source"] == "leaderboard"
    assert rows[("3", "2026-10-01")]["distance_m"] == "8000.0"


def test_expired_session_is_a_scrape_error(env):
    _, use = env
    use(_FakePage({a: {"expired": True, "status": 401} for a in "123"}))
    with pytest.raises(A.ScrapeError, match="expired"):
        A.run({})


def test_many_failed_requests_fail_the_run_but_keep_what_was_found(env):
    tmp, use = env
    use(_FakePage({"1": {"entries": [_entry(10, 1)]}, "2": {"error": "HTTP 500"}, "3": {"error": "HTTP 500"}}))
    with pytest.raises(A.ScrapeError, match="2 of 3"):
        A.run({})
    assert [r["activity_id"] for r in _read(tmp / "activities.csv")] == ["10"]


def test_a_429_stops_the_scan_and_keeps_what_was_fetched(env):
    tmp, use = env
    use(_FakePage({"1": {"entries": [_entry(10, 1)]}, "2": {"limited": True}, "3": None}))   # 3: never fetched
    with pytest.raises(A.ScrapeError, match="429"):
        A.run({})
    rows = {(r["athlete_id"], r["date"]) for r in _read(tmp / "daily.csv")}
    assert ("1", "2026-10-01") in rows and ("1", "2026-09-27") in rows
    assert [r["scraped_at"] for r in _read(tmp / "activities.csv")] == ["2026-10-01T12:00:00+00:00"]   # = synced_at
