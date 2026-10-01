"""Fill the append-only activities.csv from two Strava sources that share one parser and one writer:
  RecentActivityFeed - the club's logged-in feed (hourly). It only retains ~2.5 days, so the pipeline
                       must run often enough to never miss a window; it also sees athletes not yet in members.csv.
  ProfilesFeed       - each members.csv athlete's monthly profile chart (daily), catching older runs the feed missed.
    python -m src.activities.activities [--workers N]     # runs ProfilesFeed
"""
import argparse
import random
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import settings
from ..dashboard.generate import _local_date
from ..strava_session import (CLUB_ID, CLUB_URL, ScrapeError, StravaScraper, append_new_rows,
                              club_page, csv_column_set, read_csv, require_auth)

CSV_PATH = Path(__file__).parent / "activities.csv"
# Not imported from members.members: that module imports CSV_PATH from here (cycle).
MEMBERS_CSV = Path(__file__).parent.parent / "members" / "members.csv"
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
    """stat_one/stat_one_subtitle pairs -> {label: value}.
    Labels vary by sport, so look up by label ("Distance", "Pace"), never by position."""
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
    """One feed entry -> zero or more rows.
    "Activity" is camelCase at entry["activity"]; "GroupActivity" nests snake_case rows in rowData."""
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


def append_activities(rows: list, entity: str | None = None) -> list:
    """Append rows whose activity_id isn't in activities.csv yet, stamped with scrape time
    (and `entity`, if given); returns the rows appended. Re-reads the CSV so a concurrent run's rows are skipped."""
    seen = csv_column_set(CSV_PATH, "activity_id")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = []
    for r in rows:
        r["activity_id"] = str(r["activity_id"])
        if r["activity_id"] not in seen:
            r["scraped_at"] = now
            if entity:
                r["entity"] = entity
            seen.add(r["activity_id"])
            new.append(r)
    append_new_rows(CSV_PATH, FIELDS, new)
    return new


class RecentActivityFeed(StravaScraper):
    """Scrape the club activity feed into the append-only activities.csv."""

    landing_url = f"{CLUB_URL}/recent_activity"

    def fetch(self) -> list:
        """Page through the club feed (same XHR the page makes) until caught up; return all entries."""
        seen = csv_column_set(CSV_PATH, "activity_id")
        entries = []
        url = FEED_URL
        with club_page(self.landing_url) as page:
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
                # Strava caps each response at 100 entries, so follow the cursor while hasMore.
                if not page_entries or not (data.get("pagination") or {}).get("hasMore"):
                    return entries
                page_ids = [str(r["activity_id"]) for e in page_entries for r in normalise(e) if r["activity_id"]]
                # Newest-first: once a whole page is already in the CSV, everything older is too.
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
        new = append_activities(rows)
        print(f"{len(entries)} entries -> {len(rows)} activities, {len(new)} new -> {CSV_PATH}")
        return len(new)


# The club feed only ever carried these (plus a couple of stray Rides); profiles also
# list gym sessions, swims, rides... which would inflate the running ledger.
FOOT_TYPES = {"Run", "TrailRun", "VirtualRun", "Walk", "Hike"}

INTERVAL_URL = ("/athletes/{athlete_id}/interval?interval={month}"
                "&interval_type=month&chart_type=miles&year_offset=0")

# Fetch a batch of interval URLs with a pool of `workers` concurrent requests, all
# inside the logged-in page. Each XHR answers with jQuery calls; the month's activity
# list is the HTML string passed to jQuery('#interval-rides').html(...), whose feed
# component carries the entries in data-react-props.appContext.preFetchedEntries.
# The string literal is handed to the JS engine to unescape (its escapes aren't JSON).
# Per URL: {entries: [...]} | {entries: null} (private / no list) | {expired} | {error}.
BATCH_JS = r"""async ({urls, workers}) => {
    const sleep = (ms) => new Promise(res => setTimeout(res, ms));
    const parse = (t) => {
        const marker = "jQuery('#interval-rides').html(";
        const start = t.indexOf(marker);
        if (start < 0) return null;
        let i = start + marker.length + 1;
        while (i < t.length && t[i] !== '"') i += t[i] === '\\' ? 2 : 1;
        const html = Function('return ' + t.slice(start + marker.length, i + 1))();
        const el = new DOMParser().parseFromString(html, 'text/html').querySelector('[data-react-props]');
        return el ? (JSON.parse(el.getAttribute('data-react-props')).appContext.preFetchedEntries || []) : null;
    };
    const one = async (url) => {
        let last = '';
        for (let attempt = 0; attempt < 4; attempt++) {
            if (attempt) await sleep(2000 * 2 ** attempt + Math.random() * 1000);
            try {
                const r = await fetch(url, {credentials: 'include', headers: {'X-Requested-With': 'XMLHttpRequest'}});
                if (r.url.includes('/login') || r.status === 401) return {expired: true, status: r.status};
                if (r.status === 429 || r.status >= 500) { last = 'HTTP ' + r.status; continue; }
                if (!r.ok) return {error: 'HTTP ' + r.status};
                return {entries: parse(await r.text())};
            } catch (e) { last = String(e); }
        }
        return {error: last};
    };
    const out = new Array(urls.length);
    let next = 0;
    const worker = async () => {
        while (next < urls.length) {
            const i = next++;
            out[i] = await one(urls[i]);
            await sleep(150 + Math.random() * 350);
        }
    };
    await Promise.all(Array.from({length: Math.min(workers, urls.length)}, worker));
    return out;
}"""


def months_since(start: str, today: date) -> list:
    """Strava interval ids ("YYYYMM") from the month of ISO date `start` to today's, inclusive."""
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while (y, m) <= (today.year, today.month):
        out.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def challenge_rows(entries: list, athlete_id: str, challenge_start: str, tzinfo) -> list:
    """This athlete's public foot activities in `entries` that started locally on/after
    challenge_start, activity_id as str."""
    rows = []
    for r in (r for e in entries for r in normalise(e)):
        r["activity_id"] = str(r["activity_id"] or "")
        if (r["activity_id"]
                and str(r["athlete_id"]) == athlete_id  # group runs list others; their own pass takes them
                and r["type"] in FOOT_TYPES
                and r["visibility"] == "everyone"  # followers-only never reaches the feed or a public page
                and _local_date(r["start_date_utc"], tzinfo) >= challenge_start):
            rows.append(r)
    return rows


def _fmt_secs(s: float) -> str:
    m, s = divmod(int(s), 60)
    return f"{m}m{s:02d}s"


class ProfilesFeed:
    """Scan each members.csv athlete's monthly profile chart and append the foot activities
    the club feed missed. The month-bar XHR returns the same entries the feed carries, so
    normalise() parses them unchanged; one request per member per month."""

    def __init__(self, workers: int = 16, only: set | None = None):
        self.workers, self.only = workers, only

    def run(self) -> int:
        """Scan all members (just `only`, if given); append them; returns the missing-row count."""
        require_auth()
        tzinfo = ZoneInfo(settings.timezone)
        months = months_since(settings.challenge_start, datetime.now(tzinfo).date())
        seen = csv_column_set(CSV_PATH, "activity_id")
        members = {r["athlete_id"]: r["name"] for r in read_csv(MEMBERS_CSV)
                   if self.only is None or r["athlete_id"] in self.only}
        jobs = [(aid, m) for aid in members for m in months]

        print("Backfill from member profiles")
        print(f"  challenge start : {settings.challenge_start} ({settings.timezone})")
        print(f"  months          : {', '.join(months)}")
        print(f"  members         : {len(members)} (from {MEMBERS_CSV.name})")
        print(f"  requests        : {len(jobs)}, {self.workers} concurrent, batches of {self.workers * 6}")
        print(f"  activities.csv  : {len(seen)} rows already", flush=True)

        t0 = time.monotonic()
        with club_page() as page:
            found, hidden, errors, per_member = self._scan(page, jobs, members, seen, tzinfo)
        self._summary(members, per_member, found, hidden, errors, time.monotonic() - t0)

        count = len(found)
        if found:
            # The hourly scrape may have run during the scan; the shared writer skips what it added.
            count = len(append_activities(found, entity="ProfileBackfill"))  # tells these apart from club-feed rows
            print(f"\nAppended {count} rows -> {CSV_PATH}")
        if len(errors) > 0.02 * len(jobs):
            # >2% of requests failed. Found rows are already saved; failing lets the scheduler flag the gaps.
            raise ScrapeError(f"{len(errors)} of {len(jobs)} profile requests failed - "
                              "activities may be missing; re-run to retry.")
        return count

    def _scan(self, page, jobs, members, seen, tzinfo) -> tuple:
        """Fetch every (athlete_id, month) job in batches; -> (found, hidden, errors, per_member).
        Adds each found activity_id to `seen` so a group run listed twice is kept once."""
        found, hidden, errors = [], [], []
        per_member = {}  # athlete_id -> [on_profile, already_in_csv, missing]
        batch_size = self.workers * 6
        t0 = time.monotonic()
        for b in range(0, len(jobs), batch_size):
            batch = jobs[b:b + batch_size]
            urls = [INTERVAL_URL.format(athlete_id=a, month=m) for a, m in batch]
            results = page.evaluate(BATCH_JS, {"urls": urls, "workers": self.workers})

            for (athlete_id, month), res in zip(batch, results):
                name = members[athlete_id]
                if res.get("expired"):
                    raise ScrapeError(f"Session expired or blocked (HTTP {res['status']}) at "
                                      f"{athlete_id} {month} - re-run: python -m src.login")
                if "error" in res:
                    errors.append((athlete_id, name, month, res["error"]))
                    print(f"  ! {athlete_id} {name} {month}: failed after retries ({res['error']})")
                    continue
                if res["entries"] is None:
                    hidden.append((athlete_id, name, month))
                    continue
                rows = challenge_rows(res["entries"], athlete_id, settings.challenge_start, tzinfo)
                missing = [r for r in rows if r["activity_id"] not in seen]
                stats = per_member.setdefault(athlete_id, [0, 0, 0])
                stats[0] += len(rows)
                stats[1] += len(rows) - len(missing)
                stats[2] += len(missing)
                for r in missing:
                    seen.add(r["activity_id"])
                    print(f"  + MISSING {athlete_id} {name} [{month}]: {r['activity_id']} "
                          f"{_local_date(r['start_date_utc'], tzinfo)} {r['type']} "
                          f"{r['distance_m']} m {r['activity_name']!r}")
                found += missing

            done = b + len(batch)
            elapsed = time.monotonic() - t0
            eta = elapsed / done * (len(jobs) - done)
            print(f"[{done}/{len(jobs)} requests | {done / elapsed:.1f}/s | "
                  f"elapsed {_fmt_secs(elapsed)} | eta {_fmt_secs(eta)}] "
                  f"missing {len(found)}, hidden {len(hidden)}, errors {len(errors)}", flush=True)
        return found, hidden, errors, per_member



    def _summary(self, members, per_member, found, hidden, errors, elapsed):
        active = {a: s for a, s in per_member.items() if s[0]}
        print("\n==================== BACKFILL SUMMARY ====================")
        print(f"Time              : {_fmt_secs(elapsed)}")
        print(f"Members scanned   : {len(members)}")
        print(f"  with activities : {len(active)} (foot, since challenge start)")
        print(f"  hidden/private  : {len({a for a, _, _ in hidden})}")
        print(f"  failed requests : {len(errors)}")
        print(f"Profile activities: {sum(s[0] for s in active.values())}, "
              f"already in CSV {sum(s[1] for s in active.values())}, missing {len(found)}")
        if found:
            print("Missing by type   : " + ", ".join(f"{t} {n}" for t, n in Counter(r["type"] for r in found).most_common()))
            km = sum(r["distance_m"] or 0 for r in found) / 1000
            print(f"Missing distance  : {km:.1f} km")
            print("Members with missing activities:")
            for a, s in sorted(per_member.items(), key=lambda kv: -kv[1][2]):
                if s[2]:
                    print(f"  {a:>12} {members[a]:<30} missing {s[2]:>3} of {s[0]}")
        if errors:
            print("Failed requests (re-run to retry; already-found rows are idempotent):")
            for a, name, month, err in errors:
                print(f"  {a:>12} {name:<30} {month} {err}")
        print("==========================================================")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # athlete names may be non-ASCII
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=16, help="concurrent profile requests (default 16)")
    args = parser.parse_args()
    ProfilesFeed(max(1, args.workers)).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
