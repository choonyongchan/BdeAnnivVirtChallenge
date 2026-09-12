"""Unit tests for src/main.py's three standalone pieces (not main()'s orchestration,
which is just a straight-line call into already-covered/scraper-owned pieces).

Rules: check_auth fails fast (SystemExit) unless the saved session file has a non-empty
cookies list; publish_dashboard no-ops when the staged diff is empty, otherwise commits
and pushes with a timestamped message; report_counts_to_ci no-ops outside GitHub Actions,
otherwise writes both step outputs to GITHUB_OUTPUT.
"""
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
        ["git", "add", str(M.INDEX_HTML), str(M.USER_COUNT_JSON)],
        ["git", "diff", "--cached", "--quiet"],
    ]


def test_publish_dashboard_commits_and_pushes_when_changed(monkeypatch):
    fake_run = _FakeRun(diff_returncode=1)
    monkeypatch.setattr(M.subprocess, "run", fake_run)
    monkeypatch.setattr(M, "datetime", _FrozenClock("2026-09-14T06:38:00"))

    M.publish_dashboard()

    assert fake_run.calls == [
        ["git", "add", str(M.INDEX_HTML), str(M.USER_COUNT_JSON)],
        ["git", "diff", "--cached", "--quiet"],
        ["git", "commit", "-m", "🏃 Dashboard update 2026-09-14 06:38"],
        ["git", "push"],
    ]


def test_report_counts_to_ci_noop_without_github_output(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    M.report_counts_to_ci(3, 5)  # must not raise


@pytest.mark.parametrize("new_activities,new_members", [(0, 0), (3, 5)])
def test_report_counts_to_ci_writes_step_outputs(tmp_path, monkeypatch, new_activities, new_members):
    output_path = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    M.report_counts_to_ci(new_activities, new_members)

    assert output_path.read_text(encoding="utf-8") == (
        f"new_activities={new_activities}\nnew_members={new_members}\n"
    )
