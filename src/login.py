"""One-off: open a visible browser, log into Strava, and save the shared session
cookies (src/auth_state.json) that every scraper reuses.

    python -m src.login
"""
from .activities.activities import ActivityScraper


def main() -> None:
    ActivityScraper().login()  # the session is shared, so either scraper's login works


if __name__ == "__main__":
    main()
