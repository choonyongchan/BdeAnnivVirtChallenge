"""Scrape club activities from Strava's logged-in feed into an append-only CSV.

The public club API (getClubActivitiesByClubId) was deactivated by Strava in 2026, so
this reads the same data from the internal feed endpoint the club page itself calls.

Run via the pipeline (python -m src.main); the one-off login is python -m src.login.
The feed only retains ~2.5 days, so this must run often enough to never miss a window.
Browser session, login, and retry live in src/strava_session.py.
"""
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from ..strava_session import CLUB_ID, ScrapeError, StravaScraper, append_new_rows, csv_column_set

CSV_PATH = Path(__file__).parent / "activities.csv"
FEED_URL = f"/clubs/{CLUB_ID}/feed?feed_type=club&num_entries=100"

FIELDS = [
    "activity_id", "athlete_id", "athlete_name", "athlete_firstname",
    "start_date_utc", "start_date_local", "activity_name", "type",
    "distance_m", "moving_time_s", "elapsed_time_s", "pace", "elev_gain_m", "steps",
    "device_name", "workout_type", "is_virtual", "is_commute",
    "visibility", "location", "description", "kudos_count", "comment_count",
    "entity", "scraped_at",
]


def _text(value) -> str:
    """Strip Strava's markup: <abbr> units around stat values, <p>/<br> in descriptions.
    Descriptions carry a real newline alongside each <br />, so no structure is lost."""
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def parse_stats(stats: list) -> dict:
    """stat_one/stat_one_subtitle pairs -> {label: value}. Labels vary by sport, so
    callers must look up by label ("Distance", "Pace", ...), never by position."""
    raw = {s.get("key"): s.get("value") for s in stats or []}
    out = {}
    for key, value in raw.items():
        if key and not key.endswith("_subtitle"):
            label = _text(raw.get(f"{key}_subtitle"))
            if label:
                out[label] = _text(value)
    return out


def to_meters(s: str):
    """A '5.2 km' / '3 mi' / '400 m' distance string as rounded metres, or None."""
    m = re.search(r"([\d,.]+)\s*(km|mi|m)\b", s or "")
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return round(n * {"km": 1000, "mi": 1609.344, "m": 1}[m.group(2)], 1)


def to_seconds(s: str):
    """A '1h 5m 3s' style duration string as total seconds, or None."""
    parts = re.findall(r"(\d+)\s*([hms])", s or "")
    if not parts:
        return None
    return sum(int(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in parts)


def to_int(s: str):
    """The digits in a string as an int (drops ',', ' steps', ...), or None."""
    digits = re.sub(r"[^\d]", "", s or "")
    return int(digits) if digits else None


def _row(**kw) -> dict:
    """Build a CSV row from the fields both schemas share, filling stats generically."""
    stats = kw.pop("stats", {})
    row = dict.fromkeys(FIELDS)
    row.update(kw)
    row["distance_m"] = to_meters(stats.get("Distance"))
    row["moving_time_s"] = to_seconds(stats.get("Time"))
    row["elev_gain_m"] = to_meters(stats.get("Elev Gain"))
    row["steps"] = to_int(stats.get("Steps"))
    row["pace"] = stats.get("Pace")
    return row


def normalise(entry: dict) -> list:
    """One feed entry -> zero or more rows. Strava uses two different schemas here:
    "Activity" is camelCase at entry["activity"]; "GroupActivity" nests a list of
    snake_case activities under entry["rowData"]["activities"]."""
    entity = entry.get("entity")

    if entity == "Activity":
        a = entry.get("activity") or {}
        ath = a.get("athlete") or {}
        kc = a.get("kudosAndComments") or {}
        return [_row(
            entity=entity, stats=parse_stats(a.get("stats")),
            activity_id=a.get("id"), athlete_id=ath.get("athleteId"),
            athlete_name=ath.get("athleteName"), athlete_firstname=ath.get("firstName"),
            start_date_utc=a.get("startDate"), activity_name=a.get("activityName"),
            type=a.get("type"), elapsed_time_s=a.get("elapsedTime"),
            device_name=a.get("deviceName"), workout_type=a.get("workoutType"),
            is_virtual=a.get("isVirtual"), is_commute=a.get("isCommute"),
            visibility=a.get("visibility"), description=_text(a.get("description")),
            location=(a.get("timeAndLocation") or {}).get("location"),
            kudos_count=kc.get("kudosCount"), comment_count=len(kc.get("comments") or []),
        )]

    if entity == "GroupActivity":
        return [_row(
            entity=entity, stats=parse_stats(a.get("stats")),
            activity_id=a.get("activity_id"), athlete_id=a.get("athlete_id"),
            athlete_name=a.get("athlete_name"), athlete_firstname=a.get("athlete_firstname"),
            start_date_utc=a.get("start_date"), start_date_local=a.get("start_date_local"),
            activity_name=a.get("name"), type=a.get("type"),
            elapsed_time_s=a.get("elapsed_time"), device_name=a.get("device_name"),
            is_virtual=a.get("is_virtual"), is_commute=a.get("is_commute"),
            visibility=a.get("visibility"), description=_text(a.get("description")),
            location=a.get("location"), kudos_count=a.get("kudos_count"),
            comment_count=a.get("num_comments"),
        ) for a in ((entry.get("rowData") or {}).get("activities") or [])]

    return []


def _seen_ids() -> set:
    """activity_id of every row already in activities.csv, as strings."""
    return csv_column_set(CSV_PATH, "activity_id")


class ActivityScraper(StravaScraper):
    """Scrape the club activity feed into the append-only activities.csv."""

    landing_url = f"https://www.strava.com/clubs/{CLUB_ID}/recent_activity"

    def fetch(self) -> list:
        """Page through the club feed from inside the club page (same XHR the page makes);
        return all entries. Strava caps each response at 100 entries regardless of
        num_entries and reports pagination.hasMore, so a single fetch silently drops
        older entries still inside the ~2.5 day window - keep following the cursor
        (before/cursor, taken from the last entry's cursorData) until it doesn't.

        The feed is newest-first, so once a whole page is already in activities.csv,
        everything beyond it is old too - stop there instead of paging to hasMore's
        end every run. That keeps a normal run to a page or two while still coping
        with a sudden burst of activities: pages keep going, however many it takes,
        up to a large circuit-breaker ceiling that raises loudly instead of quietly
        truncating."""
        seen = _seen_ids()
        entries = []
        url = FEED_URL
        with self._club_page() as page:
            for _ in range(500):  # circuit breaker; ~50k entries, far above a burst hour
                result = page.evaluate(
                    """async (url) => {
                        const r = await fetch(url, {credentials: 'include'});
                        const t = await r.text();
                        try { return {ok: true, data: JSON.parse(t)}; }
                        catch { return {ok: false, snippet: t.slice(0, 200)}; }
                    }""",
                    url,
                )
                if not result["ok"]:
                    raise ScrapeError("Session expired or blocked - re-run: python -m src.login\n"
                                      f"Response was not JSON: {result['snippet'][:120]!r}")
                data = result["data"]
                page_entries = data.get("entries") or []
                entries.extend(page_entries)
                if not page_entries or not (data.get("pagination") or {}).get("hasMore"):
                    return entries
                page_ids = [str(r["activity_id"]) for e in page_entries for r in normalise(e) if r["activity_id"]]
                if page_ids and all(i in seen for i in page_ids):
                    return entries
                cursor = page_entries[-1]["cursorData"]
                url = f"{FEED_URL}&before={cursor['updated_at']}&cursor={cursor['rank']}"
                page.wait_for_timeout(random.randint(400, 900))
        raise ScrapeError(f"Feed still had more pages after {len(entries)} entries - "
                           "backlog too large for the circuit breaker, needs a look.")

    def write(self, entries: list) -> int:
        """Append every feed activity not already in activities.csv, stamped with scrape time."""
        rows = [r for e in entries for r in normalise(e) if r["activity_id"]]

        seen = _seen_ids()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new = []
        for r in rows:
            r["activity_id"] = str(r["activity_id"])
            if r["activity_id"] not in seen:
                r["scraped_at"] = now
                seen.add(r["activity_id"])
                new.append(r)

        append_new_rows(CSV_PATH, FIELDS, new)

        print(f"{len(entries)} entries -> {len(rows)} activities, {len(new)} new -> {CSV_PATH}")
        return len(new)
