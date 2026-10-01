"""One-off: log into Strava in a visible browser and save src/auth_state.json for every scraper.
    python -m src.login
"""
from .strava_session import login

if __name__ == "__main__":
    login()
