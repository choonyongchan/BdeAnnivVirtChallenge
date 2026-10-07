"""MemberActivities: every member's profile week (Mon-Sun, /athletes/{id}/interval), the heavy nightly scan.
Their activities (any sport) fill whatever the hourly feed missed in the ledger (activities.csv); the weeks' foot
activities go to Statistics. Also holds the ledger parsing the Feed shares.
    python -m backend.activities.member_activities [--setup]    # --setup: every week since challenge_start
"""
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import settings
from ..members.members import CSV_PATH as MEMBERS_CSV
from ..strava_session import ScrapeError, append_new_rows, club_page, csv_column_set, read_csv, require_auth

CSV_PATH = Path(__file__).parent / "activities.csv"
WEEK_URL = "/athletes/{athlete_id}/interval?interval={week}&interval_type=week&chart_type=miles&year_offset=0"

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


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_activities(rows: list, stamp: str | None = None) -> list:
    """Append rows whose activity_id isn't in activities.csv yet, stamped scraped_at = `stamp` (default now);
    returns the rows appended."""
    seen = csv_column_set(CSV_PATH, "activity_id")
    now = stamp or now_utc()
    new = []
    for r in rows:
        r["activity_id"] = str(r["activity_id"])
        if r["activity_id"] not in seen:
            r["scraped_at"] = now
            seen.add(r["activity_id"])
            new.append(r)
    append_new_rows(CSV_PATH, FIELDS, new)
    return new


# What the club leaderboard counts, and so Statistics: foot sports. The ledger keeps every sport.
FOOT_TYPES = {"Run", "TrailRun", "VirtualRun", "Walk", "Hike"}

# Fetch a batch of profile-week URLs with a pool of `workers` concurrent requests, all
# inside the logged-in page. Each XHR answers with jQuery calls; the week's activity
# list is the HTML string passed to jQuery('#interval-rides').html(...), whose feed
# component carries the entries in data-react-props.appContext.preFetchedEntries.
# The string literal is handed to the JS engine to unescape (its escapes aren't JSON).
# Per URL: {entries: [...]} | {entries: null} (private / no list) | {expired} | {error} | {limited} | null.
# A 429 stops every worker (unfetched URLs stay null): retrying in parallel only deepens Strava's block.
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
                if (r.status === 429) { limited = true; return {limited: true}; }
                if (r.status >= 500) { last = 'HTTP ' + r.status; continue; }
                if (!r.ok) return {error: 'HTTP ' + r.status};
                return {entries: parse(await r.text())};
            } catch (e) { last = String(e); }
        }
        return {error: last};
    };
    const out = new Array(urls.length).fill(null);
    let next = 0, limited = false;
    const worker = async () => {
        while (next < urls.length && !limited) {
            const i = next++;
            out[i] = await one(urls[i]);
            await sleep(150 + Math.random() * 350);
        }
    };
    await Promise.all(Array.from({length: Math.min(workers, urls.length)}, worker));
    return out;
}"""


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_id(monday: date) -> str:
    """Strava's interval id for a week: ISO year + week, e.g. 2026-09-14 -> "202638"."""
    y, w, _ = monday.isocalendar()
    return f"{y}{w:02d}"


def weeks_to_sync(today: date, challenge_start: str, full: bool) -> list:
    """Mondays to refresh: every week since challenge_start (full), else last week and this week,
    so runs uploaded late into the week just closed still land."""
    this = monday_of(today)
    if full:
        first = monday_of(date.fromisoformat(challenge_start))
        return [first + timedelta(weeks=i) for i in range((this - first).days // 7 + 1)]
    return [this - timedelta(weeks=1), this]   # ponytail: older weeks never re-checked; --setup repairs them


def own_rows(entries: list, athlete_id: str) -> list:
    """This athlete's own activities in a profile week's entries (group activities also list the others)."""
    rows = []
    for r in (r for e in entries for r in normalise(e)):
        r["activity_id"] = str(r["activity_id"] or "")
        if r["activity_id"] and str(r["athlete_id"]) == athlete_id:
            rows.append(r)
    return rows


def jobs_for(members: list, last_scan: str, today: date, setup: bool) -> list:
    """(athlete_id, monday) pairs to fetch: current members' last two weeks, every week since challenge_start for
    members ingested after the last nightly scan (or everyone with setup)."""
    recent = weeks_to_sync(today, settings.challenge_start, False)
    every = weeks_to_sync(today, settings.challenge_start, True)
    jobs = []
    for m in members:
        if m.get("left_at"):
            continue
        weeks = every if setup or (m.get("ingest_at") or "") > last_scan else recent
        jobs += [(m["athlete_id"], w) for w in weeks]
    return sorted(jobs, key=lambda j: (j[1], j[0]))


def run(last_scan: str, setup: bool = False, workers: int = 4) -> tuple:
    """Scan current members' profile weeks into the ledger; returns (weeks, synced_at, problem) where weeks is
    {(athlete_id, monday_iso): foot activity rows} for Statistics and problem a message when the scan fell short
    (rate-limited, or too many errors) - the caller saves what was found, then raises it. Stops at Strava's first
    429, keeping what it fetched."""
    require_auth()
    synced_at = now_utc()   # ledger rows found here share it, so Statistics never adds them twice
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    jobs = jobs_for(read_csv(MEMBERS_CSV), last_scan, today, setup)
    print(f"{len(jobs)} profile requests", flush=True)

    weeks, ledger, errors, limited = {}, [], 0, False
    batch_size = workers * 6
    with club_page() as page:
        for b in range(0, len(jobs), batch_size):
            batch = jobs[b:b + batch_size]
            urls = [WEEK_URL.format(athlete_id=a, week=week_id(m)) for a, m in batch]
            for (aid, monday), res in zip(batch, page.evaluate(BATCH_JS, {"urls": urls, "workers": workers})):
                if res is None or res.get("limited"):   # None: never fetched, a 429 stopped the batch
                    limited = True
                    continue
                if res.get("expired"):
                    raise ScrapeError(f"Session expired or blocked (HTTP {res['status']}) - re-run: python -m backend.login")
                if "error" in res:
                    errors += 1
                    continue
                rows = own_rows(res["entries"] or [], aid)  # None: private profile, nothing visible
                weeks[(aid, monday.isoformat())] = [r for r in rows if r["type"] in FOOT_TYPES]
                ledger += rows
            print(f"  [{b + len(batch)}/{len(jobs)}] errors {errors}", flush=True)
            if limited:
                break

    new = append_activities(ledger, synced_at)
    print(f"{len(new)} new activities -> {CSV_PATH}")
    problem = None
    if limited:
        problem = (f"Strava rate-limited (HTTP 429) after {len(weeks)} of {len(jobs)} profile requests; "
                   "kept what was fetched - the next full run resumes.")
    elif errors > 0.02 * len(jobs):
        problem = f"{errors} of {len(jobs)} profile requests failed - re-run to retry."
    return weeks, synced_at, problem
