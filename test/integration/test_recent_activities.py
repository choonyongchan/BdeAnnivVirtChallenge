"""Integration test for recent_activities.py's fetches (no browser): the headline count from the members page,
feed athletes across cursor pages; non-OK, count-less or non-JSON responses are ScrapeErrors."""
import json
from contextlib import contextmanager

import pytest

from backend.activities import recent_activities as M
from backend.members.members import MEMBERS_URL


class _FakePage:
    """Stands in for Playwright's page: .evaluate() answers from responses[url without cursor],
    a list consumed in order and sticky on its last; .wait_for_timeout() is a no-op."""

    def __init__(self, responses):
        self._responses = responses
        self.urls = []

    def evaluate(self, script, url):
        self.urls.append(url)
        queue = self._responses[url.split("&before=")[0]]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def wait_for_timeout(self, ms):
        pass


def _use_fake_page(monkeypatch, fake_page):
    @contextmanager
    def _fake_club_page(url):
        yield fake_page
    monkeypatch.setattr(M, "club_page", _fake_club_page)


def _resp(html, ok=True, status=200):
    return {"ok": ok, "status": status, "text": html}


def _feed(athletes, has_more, cursor=("100", "r1")):
    entries = [{"entity": "Activity", "activity": {"id": f"a{aid}", "athlete": {"athleteId": aid, "athleteName": name}},
                "cursorData": {"updated_at": cursor[0], "rank": cursor[1]}} for aid, name in athletes]
    return _resp(json.dumps({"entries": entries, "pagination": {"hasMore": has_more}}))


COUNT = _resp("<span class='membership-count'>1,055 members</span>")


def test_count_and_feed_follow_the_cursor(monkeypatch):
    fake_page = _FakePage({MEMBERS_URL: [COUNT], M.FEED_URL: [
        _feed([(1, "Alice Anon")], True), _feed([(2, "Bob Bogus"), (1, "Alice Anon")], False)]})
    _use_fake_page(monkeypatch, fake_page)

    count, athletes = M.fetch_count_and_feed()   # one row per feed activity

    assert count == 1055
    assert [(r["athlete_id"], r["athlete_name"]) for r in athletes] == [(1, "Alice Anon"), (2, "Bob Bogus"),
                                                                         (1, "Alice Anon")]
    assert fake_page.urls[2] == f"{M.FEED_URL}&before=100&cursor=r1"


def test_http_failure_is_a_scrape_error(monkeypatch):
    _use_fake_page(monkeypatch, _FakePage({MEMBERS_URL: [_resp("", ok=False, status=403)]}))
    with pytest.raises(M.ScrapeError, match="403"):
        M.fetch_count_and_feed()


def test_missing_count_is_a_scrape_error(monkeypatch):
    _use_fake_page(monkeypatch, _FakePage({MEMBERS_URL: [_resp("<p>There are no active members in this club yet.</p>")]}))
    with pytest.raises(M.ScrapeError, match="Member count not found"):
        M.fetch_count_and_feed()


def test_non_json_feed_is_a_scrape_error(monkeypatch):
    _use_fake_page(monkeypatch, _FakePage({MEMBERS_URL: [COUNT], M.FEED_URL: [_resp("<html>blocked</html>")]}))
    with pytest.raises(M.ScrapeError, match="blocked"):
        M.fetch_count_and_feed()


def test_never_ending_feed_stops_at_the_circuit_breaker(monkeypatch):
    fake_page = _FakePage({MEMBERS_URL: [COUNT], M.FEED_URL: [_feed([(1, "Alice Anon")], True)]})
    _use_fake_page(monkeypatch, fake_page)
    assert len(M.fetch_count_and_feed()[1]) == 50
    assert len(fake_page.urls) == 1 + 50
