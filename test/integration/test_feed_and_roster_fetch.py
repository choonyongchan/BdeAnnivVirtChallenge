"""Integration test for feed.py and members.py fetches (no browser): the feed across cursor pages, the members
page's headline and paged roster; non-OK, count-less or non-JSON responses are ScrapeErrors."""
import json
from contextlib import contextmanager

import pytest

from backend.activities import feed as F
from backend.members import members as M


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


def _use_fake_page(monkeypatch, module, fake_page):
    @contextmanager
    def _fake_club_page(url):
        yield fake_page
    monkeypatch.setattr(module, "club_page", _fake_club_page)


def _resp(html, ok=True, status=200):
    return {"ok": ok, "status": status, "text": html}


def _feed(athletes, has_more, cursor=("100", "r1")):
    entries = [{"entity": "Activity", "activity": {"id": f"a{aid}", "athlete": {"athleteId": aid, "athleteName": name}},
                "cursorData": {"updated_at": cursor[0], "rank": cursor[1]}} for aid, name in athletes]
    return _resp(json.dumps({"entries": entries, "pagination": {"hasMore": has_more}}))


def test_feed_follows_the_cursor(monkeypatch):
    fake_page = _FakePage({F.FEED_URL: [
        _feed([(1, "Alice Anon")], True), _feed([(2, "Bob Bogus"), (1, "Alice Anon")], False)]})
    _use_fake_page(monkeypatch, F, fake_page)

    rows = F.fetch_feed()   # one row per feed activity

    assert [(r["athlete_id"], r["athlete_name"]) for r in rows] == [(1, "Alice Anon"), (2, "Bob Bogus"),
                                                                     (1, "Alice Anon")]
    assert fake_page.urls[1] == f"{F.FEED_URL}&before=100&cursor=r1"


def test_non_json_feed_is_a_scrape_error(monkeypatch):
    _use_fake_page(monkeypatch, F, _FakePage({F.FEED_URL: [_resp("<html>blocked</html>")]}))
    with pytest.raises(F.ScrapeError, match="blocked"):
        F.fetch_feed()


def test_never_ending_feed_stops_at_the_circuit_breaker(monkeypatch):
    fake_page = _FakePage({F.FEED_URL: [_feed([(1, "Alice Anon")], True)]})
    _use_fake_page(monkeypatch, F, fake_page)
    assert len(F.fetch_feed()) == 50
    assert len(fake_page.urls) == 50


def _li(aid, name):
    return f"<li><div class='text-headline'><a href=\"/athletes/{aid}\">{name}</a></div></li>"


def _members_page(count, admins, members):
    return _resp(f"<span class='membership-count'>{count:,} members</span>"
                 "<ul class='list-athletes'>" + "".join(_li(*a) for a in admins) + "</ul>"
                 "<ul class='list-athletes'>" + "".join(_li(*m) for m in members) + "</ul>")


class _RosterPage(_FakePage):
    """Answers /members?page=n from pages[n-1]; past the last page, a page with only the admins."""

    def __init__(self, pages):
        super().__init__({})
        self.pages = pages

    def evaluate(self, script, url):
        self.urls.append(url)
        n = int(url.split("page=")[1])
        return self.pages[min(n, len(self.pages)) - 1]


def test_roster_walks_pages_until_nobody_new():
    admins = [("1", "Admin")]
    pg = _RosterPage([_members_page(4, admins, [("2", "B"), ("3", "C")]),
                      _members_page(4, admins, [("4", "D")]),
                      _members_page(4, admins, [])])
    count, roster = M.fetch_count_and_roster(pg)
    assert count == 4 and set(roster) == {"1", "2", "3", "4"}
    assert len(pg.urls) == 3


def test_http_failure_is_a_scrape_error():
    with pytest.raises(M.ScrapeError, match="403"):
        M.fetch_count_and_roster(_RosterPage([_resp("", ok=False, status=403)]))


def test_missing_count_is_a_scrape_error():
    with pytest.raises(M.ScrapeError, match="Member count not found"):
        M.fetch_count_and_roster(_RosterPage([_resp("<p>There are no active members in this club yet.</p>")]))
