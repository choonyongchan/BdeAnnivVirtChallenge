"""Shared logged-in Strava browser session for the CSV scrapers.

Strava deactivated the public club API in 2026, so src/activities/activities.py
and src/members/members.py read the same data the club pages fetch for
themselves. Both need one logged-in browser context, the same anti-bot
precautions, and the same retry policy; that lives here as StravaScraper.

    python -m src.login   # once: log in, save the shared session cookies
"""
import csv
import random
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import settings

CLUB_ID = settings.club_id                             # the club every scraper reports on
BROWSER_CHANNEL = settings.browser_channel             # from src/config.yaml; "" -> bundled Chromium
AUTH_PATH = Path(__file__).parent / "auth_state.json"  # shared session-cookie store
LOGIN_URL = "https://www.strava.com/login"             # redirects to /dashboard on success


class ScrapeError(RuntimeError):
    """A scrape could not complete: no saved session, blocked, or changed markup."""


def csv_column_set(path: Path, field: str) -> set:
    """Every value of `field` already in the CSV at path, as a set of strings.
    Empty set if the file doesn't exist yet."""
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as f:
        return {r[field] for r in csv.DictReader(f)}


def append_new_rows(path: Path, fields: list, rows: list) -> None:
    """Append rows to the CSV at path, writing the header first if the file is new."""
    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            w.writeheader()
        w.writerows(rows)


class StravaScraper:
    """Base scraper: one logged-in browser session plus retry/backoff around fetch()."""

    #: Same-origin page to open before running in-page fetches (sets the referer).
    landing_url = f"https://www.strava.com/clubs/{CLUB_ID}"

    def _new_context(self, pw, headless):
        """Return (browser, context) that looks like ordinary browsing (UA/locale/tz spoofed)."""
        browser = pw.chromium.launch(
            headless=headless, channel=BROWSER_CHANNEL or None,
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Headless otherwise advertises "HeadlessChrome/..." — the most obvious bot tell.
        # Derived from the live browser so it tracks Chromium updates; major only, because
        # real Chromium freezes the UA to <major>.0.0.0 (UA reduction).
        ver = f"{browser.version.split('.')[0]}.0.0.0"
        ctx = browser.new_context(
            storage_state=str(AUTH_PATH) if AUTH_PATH.exists() else None,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        f"(KHTML, like Gecko) Chrome/{ver} Safari/537.36 Edg/{ver}"),
            locale="en-SG", timezone_id="Asia/Singapore",
            viewport={"width": 1512, "height": 856},
        )
        return browser, ctx

    @contextmanager
    def _club_page(self):
        """Open landing_url so in-page fetches carry its headers, yield the page, always close."""
        with sync_playwright() as pw:
            browser, ctx = self._new_context(pw, headless=settings.browser_headless)
            try:
                page = ctx.new_page()
                page.goto(self.landing_url, wait_until="domcontentloaded")
                page.wait_for_timeout(random.randint(1500, 4000))
                yield page
            finally:
                browser.close()

    def login(self):
        """Open a visible browser, wait up to 5 min for a manual login, save the session."""
        with sync_playwright() as pw:
            browser, ctx = self._new_context(pw, headless=False)
            page = ctx.new_page()
            page.goto(LOGIN_URL)
            print("Log into Strava in the browser window (waiting up to 5 minutes)...")
            page.wait_for_url("**/dashboard**", timeout=300_000)
            ctx.storage_state(path=str(AUTH_PATH))
            browser.close()
        print(f"Session saved to {AUTH_PATH}")

    def scrape(self) -> int:
        """Auth-check, run fetch() with 3 attempts + exponential backoff, then write().
        Returns the number of new rows write() appended."""
        if not AUTH_PATH.exists():
            raise ScrapeError("No saved session. Run: python -m src.login")
        for attempt in range(3):
            try:
                payload = self.fetch()
                break
            except ScrapeError:
                raise  # a definite failure (expired session, changed markup) — don't retry
            except Exception as e:
                if attempt == 2:
                    raise ScrapeError(f"Failed after 3 attempts: {e}") from e
                time.sleep(30 * 2 ** attempt)  # back off, never hammer
        return self.write(payload)

    def fetch(self):
        """Subclass: pull the raw data from inside the club page and return it."""
        raise NotImplementedError

    def write(self, payload) -> int:
        """Subclass: merge the payload into this scraper's append-only CSV.
        Returns the number of new rows appended."""
        raise NotImplementedError
