"""Central configuration loaded from .env file."""
import os
import sys
from dotenv import load_dotenv


class Config:
    def __init__(self):
        load_dotenv()

        # Required — Strava API
        self.strava_client_id = os.getenv("STRAVA_CLIENT_ID", "")
        self.strava_client_secret = os.getenv("STRAVA_CLIENT_SECRET", "")
        self.strava_refresh_token = os.getenv("STRAVA_REFRESH_TOKEN", "")
        self.strava_club_id = os.getenv("STRAVA_CLUB_ID", "")

        self._validate()

        # Optional — Dashboard customization
        self.club_name = os.getenv("CLUB_NAME", "8SAB 50th Anniversary Virtual Challenge")
        self.weather_lat = os.getenv("WEATHER_LAT", "1.3835")
        self.weather_lon = os.getenv("WEATHER_LON", "103.7478")
        self.timezone = os.getenv("TIMEZONE", "Asia/Singapore")
        self.start_date = os.getenv("START_DATE", "")

    def _validate(self):
        required = {
            "STRAVA_CLIENT_ID": self.strava_client_id,
            "STRAVA_CLIENT_SECRET": self.strava_client_secret,
            "STRAVA_REFRESH_TOKEN": self.strava_refresh_token,
            "STRAVA_CLUB_ID": self.strava_club_id,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            print(f"ERROR: Missing required config: {', '.join(missing)}")
            print("Set them in .env (local) or GitHub Secrets (Actions).")
            print("Run 'python3 setup_strava.py' to get Strava credentials.")
            sys.exit(0)  # exit 0 so GitHub Actions doesn't report failure


config = Config()
