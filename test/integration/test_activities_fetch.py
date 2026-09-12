"""Integration test for ActivityScraper.fetch()'s paging logic (no browser).

Rules: follow pagination.hasMore by building the next url from the last entry's
cursorData; stop early once a whole page is already in activities.csv (the feed is
newest-first, so everything beyond it is old too); a non-JSON response is a definite
ScrapeError; a feed that never stops is a ScrapeError after the circuit-breaker ceiling.
"""
import csv
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest

from src.activities import activities as A

FIXTURE = Path(__file__).parent.parent / "fixtures" / "activities_sample.csv"


def _fixture_rows():
    with FIXTURE.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class _FakePage:
    """Stands in for Playwright's page: .evaluate() returns the next canned result
    (sticky on the last one, so a test can force many iterations without listing them
    all); .wait_for_timeout() is a no-op."""

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
    monkeypatch.setattr(A.ActivityScraper, "_club_page", _fake_club_page)


def _entry(row, cursor=("100", "r1")):
    return {
        "entity": "Activity",
        "activity": {
            "id": row["activity_id"], "athlete": {"athleteId": row["athlete_id"]},
            "startDate": row["start_date_utc"], "stats": [],
        },
        "cursorData": {"updated_at": cursor[0], "rank": cursor[1]},
    }


def _page(entries, has_more):
    return {"ok": True, "data": {"entries": entries, "pagination": {"hasMore": has_more}}}


def test_fetch_returns_entries_single_page_no_more(monkeypatch):
    row = _fixture_rows()[0]
    fake_page = _FakePage([_page([_entry(row)], False)])
    _use_fake_page(monkeypatch, fake_page)

    entries = A.ActivityScraper().fetch()

    assert [e["activity"]["id"] for e in entries] == [row["activity_id"]]
    assert len(fake_page.urls) == 1


def test_fetch_follows_cursor_across_pages(monkeypatch):
    rows = _fixture_rows()[:2]
    fake_page = _FakePage([
        _page([_entry(rows[0], cursor=("100", "r1"))], True),
        _page([_entry(rows[1])], False),
    ])
    _use_fake_page(monkeypatch, fake_page)

    entries = A.ActivityScraper().fetch()

    assert [e["activity"]["id"] for e in entries] == [rows[0]["activity_id"], rows[1]["activity_id"]]
    assert fake_page.urls[1] == f"{A.FEED_URL}&before=100&cursor=r1"


def test_fetch_stops_when_whole_page_already_seen(tmp_path, monkeypatch):
    csv_path = tmp_path / "activities.csv"
    shutil.copy(FIXTURE, csv_path)
    monkeypatch.setattr(A, "CSV_PATH", csv_path)

    seen_row = _fixture_rows()[0]
    fake_page = _FakePage([_page([_entry(seen_row)], True)])  # hasMore True, but already seen
    _use_fake_page(monkeypatch, fake_page)

    entries = A.ActivityScraper().fetch()

    assert [e["activity"]["id"] for e in entries] == [seen_row["activity_id"]]
    assert len(fake_page.urls) == 1  # early exit, cursor never followed


def test_fetch_raises_scrape_error_on_non_json_response(monkeypatch):
    fake_page = _FakePage([{"ok": False, "snippet": "<html>blocked</html>"}])
    _use_fake_page(monkeypatch, fake_page)

    with pytest.raises(A.ScrapeError, match="blocked"):
        A.ActivityScraper().fetch()


def test_fetch_raises_scrape_error_after_circuit_breaker(monkeypatch):
    # Entries with no nested "activity" key normalise to activity_id=None, so page_ids
    # is always empty and the "whole page already seen" early-exit guard never fires.
    never_ending = _page([{"entity": "Activity", "cursorData": {"updated_at": "100", "rank": "r1"}}], True)
    fake_page = _FakePage([never_ending])
    _use_fake_page(monkeypatch, fake_page)

    with pytest.raises(A.ScrapeError, match="circuit breaker"):
        A.ActivityScraper().fetch()

    assert len(fake_page.urls) == 500
