"""Integration test for MemberScraper.fetch() (no browser).

Rules: one request to the members page; its headline count is returned; a non-OK
HTTP response or a page without the count is a definite ScrapeError.
"""
from contextlib import contextmanager

import pytest

from src.members import members as M


class _FakePage:
    def __init__(self, response):
        self._response = response
        self.urls = []

    def evaluate(self, script, url):
        self.urls.append(url)
        return self._response


def _use_fake_page(monkeypatch, fake_page):
    @contextmanager
    def _fake_club_page(self):
        yield fake_page
    monkeypatch.setattr(M.MemberScraper, "_club_page", _fake_club_page)


def _resp(html, ok=True, status=200):
    return {"ok": ok, "status": status, "text": html}


def test_fetch_returns_headline_count_from_one_request(monkeypatch):
    fake_page = _FakePage(_resp("<span class='membership-count'>1,055 members</span>"))
    _use_fake_page(monkeypatch, fake_page)

    assert M.MemberScraper().fetch() == 1055
    assert fake_page.urls == [M.MEMBERS_URL]


def test_fetch_raises_scrape_error_on_http_failure(monkeypatch):
    _use_fake_page(monkeypatch, _FakePage(_resp("", ok=False, status=403)))

    with pytest.raises(M.ScrapeError, match="403"):
        M.MemberScraper().fetch()


def test_fetch_raises_scrape_error_when_count_missing(monkeypatch):
    _use_fake_page(monkeypatch, _FakePage(_resp("<p>There are no active members in this club yet.</p>")))

    with pytest.raises(M.ScrapeError, match="Member count not found"):
        M.MemberScraper().fetch()
