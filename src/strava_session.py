"""Shared logged-in Strava browser session, retry policy and CSV helpers for the scrapers.
Strava deactivated the public club API in 2026, so scrapers read what the club pages fetch.
"""
import csv
import random
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import settings

CLUB_ID = settings.club_id                             # the club every scraper reports on
CLUB_URL = f"https://www.strava.com/clubs/{CLUB_ID}"
AUTH_PATH = Path(__file__).parent / "auth_state.json"  # shared session-cookie store
LOGIN_URL = "https://www.strava.com/login"             # redirects to /dashboard on success


class ScrapeError(RuntimeError):
    """A scrape could not complete: no saved session, blocked, or changed markup."""


def read_csv(path: Path) -> list:
    """Every row of the CSV at path as a dict; [] if the file doesn't exist yet."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def csv_column_set(path: Path, field: str) -> set:
    """Every value of `field` already in the CSV at path."""
    return {r[field] for r in read_csv(path)}


def append_new_rows(path: Path, fields: list, rows: list) -> None:
    """Append rows to the CSV at path, writing the header first if the file is new."""
    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            w.writeheader()
        w.writerows(rows)


def require_auth() -> None:
    """Raise ScrapeError unless a saved session exists."""
    if not AUTH_PATH.exists():
        raise ScrapeError("No saved session. Run: python -m src.login")


def _new_context(pw, headless):
    """Return (browser, context) that looks like ordinary browsing (UA/locale/tz spoofed)."""
    browser = pw.chromium.launch(
        headless=headless, channel=settings.browser_channel or None,  # "" -> bundled Chromium
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
def club_page(landing_url: str = CLUB_URL):
    """Yield a logged-in page on landing_url (so in-page fetches carry its headers); always close."""
    with sync_playwright() as pw:
        browser, ctx = _new_context(pw, headless=settings.browser_headless)
        try:
            page = ctx.new_page()
            page.goto(landing_url, wait_until="domcontentloaded")
            page.wait_for_timeout(random.randint(1500, 4000))
            yield page
        finally:
            browser.close()


def login() -> None:
    """Open a visible browser, wait up to 5 min for a manual login, save the session."""
    with sync_playwright() as pw:
        browser, ctx = _new_context(pw, headless=False)
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        print("Log into Strava in the browser window (waiting up to 5 minutes)...")
        page.wait_for_url("**/dashboard**", timeout=300_000)
        ctx.storage_state(path=str(AUTH_PATH))
        browser.close()
    print(f"Session saved to {AUTH_PATH}")

