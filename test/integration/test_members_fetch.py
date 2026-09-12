"""Integration test for MemberScraper.fetch()'s paging logic (no browser).

Rules: stop after two consecutive pages that add no *new* athlete id (a repeat counts
as dry); a non-OK HTTP response is a definite ScrapeError; parsing zero members across
the whole crawl is a ScrapeError; a 60-page safety ceiling silently caps the crawl.
"""
import csv
from contextlib import contextmanager
from pathlib import Path

import pytest

from src.members import members as M

FIXTURE = Path(__file__).parent.parent / "fixtures" / "members_sample.csv"


def _fixture_rows():
    with FIXTURE.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class _FakePage:
    def __init__(self, responses):
        self._responses = list(responses)
        self.urls = []

    def evaluate(self, script, url):
        self.urls.append(url)
        i = min(len(self.urls) - 1, len(self._responses) - 1)
        return self._responses[i]

    def wait_for_timeout(self, ms):
        pass


def _use_fake_page(monkeypatch, fake_page):
    @contextmanager
    def _fake_club_page(self):
        yield fake_page
    monkeypatch.setattr(M.MemberScraper, "_club_page", _fake_club_page)


def _html(*ids_and_names):
    items = "".join(f'<li><a href="/athletes/{aid}">{name}</a></li>' for aid, name in ids_and_names)
    return f"<ul class='list-athletes'>{items}</ul>"


def _resp(html, ok=True, status=200):
    return {"ok": ok, "status": status, "text": html}


def test_fetch_stops_after_two_dry_pages(monkeypatch):
    a, b = _fixture_rows()[:2]
    fake_page = _FakePage([
        _resp(_html((a["athlete_id"], a["name"]), (b["athlete_id"], b["name"]))),
        _resp(_html()),
        _resp(_html()),
    ])
    _use_fake_page(monkeypatch, fake_page)

    members = M.MemberScraper().fetch()

    assert members == {a["athlete_id"]: a["name"], b["athlete_id"]: b["name"]}
    assert len(fake_page.urls) == 3


def test_fetch_dedupe_across_pages_counts_as_dry(monkeypatch):
    a = _fixture_rows()[0]
    fake_page = _FakePage([
        _resp(_html((a["athlete_id"], a["name"]))),
        _resp(_html((a["athlete_id"], a["name"]))),  # repeats athlete_id, no new members
        _resp(_html()),
    ])
    _use_fake_page(monkeypatch, fake_page)

    members = M.MemberScraper().fetch()

    assert members == {a["athlete_id"]: a["name"]}
    assert len(fake_page.urls) == 3


def test_fetch_raises_scrape_error_on_http_failure(monkeypatch):
    fake_page = _FakePage([_resp("", ok=False, status=403)])
    _use_fake_page(monkeypatch, fake_page)

    with pytest.raises(M.ScrapeError, match="403"):
        M.MemberScraper().fetch()


def test_fetch_raises_scrape_error_when_nothing_parsed(monkeypatch):
    fake_page = _FakePage([_resp(_html()), _resp(_html())])
    _use_fake_page(monkeypatch, fake_page)

    with pytest.raises(M.ScrapeError, match="No members parsed"):
        M.MemberScraper().fetch()


def test_fetch_stops_at_sixty_page_ceiling(monkeypatch):
    responses = [_resp(_html((str(900000000 + n), f"Page{n}"))) for n in range(1, 61)]
    fake_page = _FakePage(responses)
    _use_fake_page(monkeypatch, fake_page)

    members = M.MemberScraper().fetch()

    assert len(members) == 60
    assert len(fake_page.urls) == 60
