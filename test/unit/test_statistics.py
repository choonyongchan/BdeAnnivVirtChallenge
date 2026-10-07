"""Unit tests for statistics.py: the leaderboard fetch (both tabs, empty = expired session), the ledger fallback's
filters (foot sports from challenge_start only) and when the last nightly scan synced."""
from contextlib import contextmanager
from datetime import date

import pytest

from backend.statistics import statistics as S


class _BoardPage:
    """Answers ROWS_JS with this week's rows, then last week's once the Last Week tab is clicked."""

    def __init__(self, this_week, last_week):
        self.rows, self.last_week, self.clicked = this_week, last_week, []

    def evaluate(self, script):
        return self.rows

    def locator(self, selector):
        page = self

        class _Tab:
            def click(self):
                page.clicked.append(selector)
                page.rows = page.last_week
        return _Tab()

    def wait_for_timeout(self, ms):
        pass


def _use(monkeypatch, page):
    @contextmanager
    def _club_page(url):
        yield page
    monkeypatch.setattr(S, "club_page", _club_page)


ROW = {"id": "7", "name": "Alice", "dist": "5.0 km", "acts": "1", "elev": "10 m", "time": "30m"}


def test_leaderboard_reads_this_week_then_last_week(monkeypatch):
    page = _BoardPage([ROW], [dict(ROW, dist="8.0 km")])
    _use(monkeypatch, page)
    board = S.fetch_leaderboard()
    this, last = sorted(board, reverse=True)
    assert date.fromisoformat(this).weekday() == 0                     # keyed by Monday
    assert board[this]["7"]["distance_m"] == 5000.0
    assert board[last]["7"]["distance_m"] == 8000.0
    assert page.clicked == ["span.button.last-week"]


def test_empty_leaderboard_is_a_scrape_error(monkeypatch):
    _use(monkeypatch, _BoardPage([], []))
    with pytest.raises(S.ScrapeError, match="Leaderboard empty"):
        S.fetch_leaderboard()


def test_local_date_handles_bad_input():
    from zoneinfo import ZoneInfo
    sgt = ZoneInfo("Asia/Singapore")
    assert S.local_date("2026-09-13T20:00:00Z", sgt) == "2026-09-14"
    assert S.local_date("not a date", sgt) == ""
    assert S.local_date(None, sgt) == ""


def _act(aid, type_="Run", start="2026-09-20T01:00:00Z", scraped="2026-09-20T05:00:00+00:00"):
    return {"athlete_id": aid, "type": type_, "start_date_utc": start, "scraped_at": scraped,
            "distance_m": "5000", "moving_time_s": "1500", "elev_gain_m": "10"}


def test_fallback_ignores_other_sports_and_runs_before_the_challenge():
    acts = [_act("1", "Ride"), _act("1", start="2026-09-10T01:00:00Z"), _act("2")]
    daily = S.fallback({}, acts, "T", date(2026, 9, 21))
    assert list(daily) == [("2", "2026-09-21")]
    assert daily[("2", "2026-09-21")]["activities"] == 1 and daily[("2", "2026-09-21")]["source"] == "feed"


def test_last_profile_sync_is_the_latest_profile_stamp(tmp_path, monkeypatch):
    path = tmp_path / "statistics.csv"
    monkeypatch.setattr(S, "CSV_PATH", path)
    assert S.last_profile_sync() == ""                                   # never scanned
    path.write_text("athlete_id,date,distance_m,moving_time_s,elev_gain_m,activities,source,synced_at\n"
                    "1,2026-09-20,1,1,1,1,profile,2026-09-20T15:00:00+00:00\n"
                    "1,2026-09-21,1,1,1,1,leaderboard,2026-09-21T15:00:00+00:00\n"
                    "2,2026-09-21,1,1,1,1,profile,2026-09-21T14:00:00+00:00\n", encoding="utf-8")
    assert S.last_profile_sync() == "2026-09-21T14:00:00+00:00"
