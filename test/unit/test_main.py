"""Unit tests for main.py: check_auth fails fast without cookies, which jobs run at which hour, a short scan saving
then failing."""
from datetime import datetime

import pytest

import backend.main as M


class _FrozenClock:
    """Stand-in for the module's `datetime`, so the hour is deterministic."""
    def __init__(self, iso):
        self._iso = iso

    def now(self, tz=None):
        return datetime.fromisoformat(self._iso)


CHECK_AUTH_CASES = [
    pytest.param(None, True, id="file_missing"),
    pytest.param("not json", True, id="not_json"),
    pytest.param("{}", True, id="no_cookies_key"),
    pytest.param('{"cookies": []}', True, id="empty_cookies"),
    pytest.param('{"cookies": [{"name": "x"}]}', False, id="valid_session"),
]


@pytest.mark.parametrize("content,should_raise", CHECK_AUTH_CASES)
def test_check_auth(tmp_path, monkeypatch, content, should_raise):
    path = tmp_path / "auth_state.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(M, "AUTH_PATH", path)

    if should_raise:
        with pytest.raises(SystemExit):
            M.check_auth()
    else:
        M.check_auth()


@pytest.fixture
def pipeline(monkeypatch):
    """Every scraper replaced by a recorder; returns (seen calls, set the hour/argv/scan result)."""
    seen = []
    monkeypatch.setattr(M, "check_auth", lambda: None)
    for key in ("members_hours", "feed_hours", "leaderboard_hours"):
        monkeypatch.setattr(M.settings, key, "*")
    monkeypatch.setattr(M.settings, "member_scan_hours", [23])
    monkeypatch.setattr(M.members, "run", lambda: seen.append("members"))
    monkeypatch.setattr(M.feed, "run", lambda: seen.append("feed"))
    monkeypatch.setattr(M.statistics, "last_profile_sync", lambda: "L")
    monkeypatch.setattr(M.statistics, "fetch_leaderboard", lambda: seen.append("leaderboard") or {"w": {}})
    monkeypatch.setattr(M.statistics, "run", lambda weeks, board, synced_at, today:
                        seen.append(f"statistics {sorted(weeks)} {sorted(board)}"))
    monkeypatch.setattr(M.generate, "run", lambda: seen.append("generate"))

    def at(hour, argv=(), problem=None):
        monkeypatch.setattr(M, "datetime", _FrozenClock(f"2026-10-02T{hour:02}:45:00"))
        monkeypatch.setattr(M.sys, "argv", ["main", *argv])
        monkeypatch.setattr(M.member_activities, "run", lambda last_scan, setup=False: seen.append(
            f"profiles {last_scan} setup={setup}") or ({("1", "m"): []}, "S", problem))
    return seen, at


@pytest.mark.parametrize("hour,argv,calls", [
    (14, [], ["members", "feed", "leaderboard", "statistics [] ['w']", "generate"]),           # hourly
    (23, [], ["members", "feed", "profiles L setup=False", "leaderboard",                       # member_scan hour
              "statistics [('1', 'm')] ['w']", "generate"]),
    (14, ["--full"], ["members", "feed", "profiles L setup=False", "leaderboard",
                      "statistics [('1', 'm')] ['w']", "generate"]),
    (14, ["--setup"], ["members", "feed", "profiles L setup=True", "leaderboard",
                       "statistics [('1', 'm')] ['w']", "generate"]),
])
def test_main_runs_what_the_schedule_says_is_due(pipeline, hour, argv, calls):
    seen, at = pipeline
    at(hour, argv)
    M.main()
    assert seen == calls


def test_jobs_off_the_schedule_are_skipped(pipeline, monkeypatch):
    seen, at = pipeline
    at(14)
    monkeypatch.setattr(M.settings, "members_hours", [3])
    monkeypatch.setattr(M.settings, "leaderboard_hours", [3])
    M.main()
    assert seen == ["feed", "statistics [] []", "generate"]


def test_a_short_scan_saves_statistics_then_stops_the_pipeline(pipeline):
    seen, at = pipeline
    at(23, problem="Strava rate-limited (HTTP 429)")
    with pytest.raises(SystemExit, match="429"):
        M.main()
    assert seen[-1].startswith("statistics") and "generate" not in seen
