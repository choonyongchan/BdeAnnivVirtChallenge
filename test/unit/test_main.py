"""Unit tests for main.py's standalone pieces: check_auth fails fast without cookies, publish_dashboard
no-ops on an empty diff."""
from datetime import datetime
from types import SimpleNamespace

import pytest

import src.main as M


class _FrozenClock:
    """Stand-in for the module's `datetime`, so the commit message is deterministic."""
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


class _FakeRun:
    def __init__(self, diff_returncode):
        self.calls = []
        self._diff_returncode = diff_returncode

    def __call__(self, cmd, cwd=None, check=False):
        self.calls.append(cmd)
        returncode = self._diff_returncode if cmd[1] == "diff" else 0
        return SimpleNamespace(returncode=returncode)


def test_publish_dashboard_noop_when_index_unchanged(monkeypatch):
    fake_run = _FakeRun(diff_returncode=0)
    monkeypatch.setattr(M.subprocess, "run", fake_run)

    M.publish_dashboard()

    assert fake_run.calls == [
        ["git", "add", str(M.generate.OUT_PATH), str(M.generate.USER_COUNT_PATH)],
        ["git", "diff", "--cached", "--quiet"],
    ]


def test_publish_dashboard_commits_and_pushes_when_changed(monkeypatch):
    fake_run = _FakeRun(diff_returncode=1)
    monkeypatch.setattr(M.subprocess, "run", fake_run)
    monkeypatch.setattr(M, "datetime", _FrozenClock("2026-09-14T06:38:00"))

    M.publish_dashboard()

    assert fake_run.calls == [
        ["git", "add", str(M.generate.OUT_PATH), str(M.generate.USER_COUNT_PATH)],
        ["git", "diff", "--cached", "--quiet"],
        ["git", "commit", "-m", "🏃 Dashboard update 2026-09-14 06:38"],
        ["git", "push"],
    ]


@pytest.mark.parametrize("hour,argv,new,calls", [
    (14, [], [], ["recent"]),                                    # hourly: club feed only
    (14, [], ["7"], ["recent", "members ['7']"]),                # a new member: their every week, straight away
    (23, [], [], ["leaderboard", "recent", "members None"]),     # config.yaml member_scan hour
    (14, ["--full"], ["7"], ["leaderboard", "recent", "members ['7']", "members None"]),
])
def test_main_runs_what_the_schedule_says_is_due(monkeypatch, hour, argv, new, calls):
    seen = []
    monkeypatch.setattr(M, "check_auth", lambda: None)
    monkeypatch.setattr(M, "datetime", _FrozenClock(f"2026-10-02T{hour:02}:47:00"))
    monkeypatch.setattr(M.sys, "argv", ["main", *argv])
    monkeypatch.setattr(M.settings, "recent_activities_hours", "*")
    monkeypatch.setattr(M.settings, "member_scan_hours", [23])
    monkeypatch.setattr(M.member_activities, "fetch_leaderboard", lambda: seen.append("leaderboard") or {})
    monkeypatch.setattr(M.recent_activities, "run", lambda board: seen.append("recent") or new)
    monkeypatch.setattr(M.member_activities, "run", lambda board, only=None: seen.append(f"members {only}"))
    monkeypatch.setattr(M.generate, "run", lambda: None)
    monkeypatch.setattr(M, "publish_dashboard", lambda: None)
    M.main()
    assert seen == calls
