"""Integration test for StravaScraper.scrape()'s auth-check and retry policy.

Rules: no saved session -> ScrapeError before any fetch; a transient failure is
retried up to 3 times with 30s then 60s backoff, then given up as a
ScrapeError; a ScrapeError from fetch() is a definite failure and is re-raised
at once without retrying; a fetch() that eventually succeeds has its payload
handed to write().
"""
import pytest

from src import strava_session as S


class _Scraper(S.StravaScraper):
    def __init__(self, effects):
        self._effects = list(effects)          # each: an Exception to raise, or a value to return
        self.fetch_calls = 0
        self.written = []

    def fetch(self):
        self.fetch_calls += 1
        effect = self._effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect

    def write(self, payload):
        self.written.append(payload)


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(S.time, "sleep", slept.append)
    return slept


@pytest.fixture
def saved_session(tmp_path, monkeypatch):
    path = tmp_path / "auth_state.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(S, "AUTH_PATH", path)
    return path


def test_no_saved_session_raises_before_fetching(tmp_path, monkeypatch, no_sleep):
    monkeypatch.setattr(S, "AUTH_PATH", tmp_path / "missing.json")
    scraper = _Scraper(["never reached"])
    with pytest.raises(S.ScrapeError):
        scraper.scrape()
    assert scraper.fetch_calls == 0 and no_sleep == []


def test_transient_failure_retries_three_times_then_gives_up(saved_session, no_sleep):
    scraper = _Scraper([RuntimeError("blip"), RuntimeError("blip"), RuntimeError("blip")])
    with pytest.raises(S.ScrapeError):
        scraper.scrape()
    assert scraper.fetch_calls == 3
    assert no_sleep == [30, 60]                 # 30 * 2**0, 30 * 2**1
    assert scraper.written == []


def test_scrape_error_from_fetch_is_not_retried(saved_session, no_sleep):
    scraper = _Scraper([S.ScrapeError("expired")])
    with pytest.raises(S.ScrapeError):
        scraper.scrape()
    assert scraper.fetch_calls == 1 and no_sleep == []


def test_success_after_retries_writes_the_payload(saved_session, no_sleep):
    scraper = _Scraper([RuntimeError("blip"), RuntimeError("blip"), {"entries": [1, 2]}])
    scraper.scrape()
    assert scraper.fetch_calls == 3
    assert no_sleep == [30, 60]
    assert scraper.written == [{"entries": [1, 2]}]
