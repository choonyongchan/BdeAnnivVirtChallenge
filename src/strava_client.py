"""Strava OAuth token refresh + club API calls, on top of stravalib."""
from stravalib import Client, exc


class StravaAuthError(Exception):
    """Raised when Strava authentication fails."""


CLUB_ACTIVITIES_PATH = "clubs/{club_id}/activities"
CLUB_MEMBERS_PATH = "clubs/{club_id}/members"


class StravaClient:
    def __init__(self, config):
        self._config = config
        self.access_token = None

    def get_access_token(self) -> str:
        """Exchange refresh token for a fresh access token."""
        try:
            token = Client().refresh_access_token(
                client_id=self._config.strava_client_id,
                client_secret=self._config.strava_client_secret,
                refresh_token=self._config.strava_refresh_token,
            )
        except exc.AccessUnauthorized:
            raise StravaAuthError(
                "Strava returned 401 — client ID or secret is incorrect.\n"
                "Check STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET."
            )
        except exc.Fault as e:
            raise StravaAuthError(
                "Strava returned an error — refresh token is likely expired or invalid.\n"
                "Fix: run 'python3 setup_strava.py' to get a new token,\n"
                "then update STRAVA_REFRESH_TOKEN in GitHub Secrets.\n"
                f"Details: {e}"
            )
        except Exception as e:
            raise StravaAuthError(f"Strava auth failed: {e}")

        self.access_token = token["access_token"]
        return self.access_token

    # ponytail: the typed Client.get_club_activities()/get_club_members() parse
    # results into pydantic models that drop undeclared fields (e.g. device_name,
    # which Strava sends but doesn't document). Use the untyped transport so raw
    # dicts with every field survive into the ledger untouched.

    def fetch_club_activities(self, per_page: int = 200) -> list:
        """Fetch the first page of club activities — Strava's own pagination is
        unreliable past page 1, so only that page is fetched."""
        client = Client(access_token=self.access_token, refresh_token=self._config.strava_refresh_token)
        path = CLUB_ACTIVITIES_PATH.format(club_id=self._config.strava_club_id)
        return client.protocol.get(path, page=1, per_page=per_page) or []

    def fetch_club_members(self) -> list:
        """Fetch club member list (firstname, lastname, id)."""
        client = Client(access_token=self.access_token, refresh_token=self._config.strava_refresh_token)
        path = CLUB_MEMBERS_PATH.format(club_id=self._config.strava_club_id)
        members = []
        page = 1

        while True:
            batch = client.protocol.get(path, page=page, per_page=200)
            if not batch:
                break
            members.extend(batch)
            if len(batch) < 200:
                break
            page += 1

        return members
