"""One-off: log into Strava in a visible browser and save backend/auth_state.json for every scraper.
    python -m backend.login
"""
from .strava_session import login

if __name__ == "__main__":
    login()
