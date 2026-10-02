"""MemberActivities: every member's profile week (Mon-Sun), the heavy but accurate scan. Their foot activities
fill gaps in the ledger (activities.csv) and feed MemberStatistics (daily.csv), along with the club leaderboard
(top 100, this and last week), which also counts runs this account can't see (followers-only, private profiles).
Also holds the ledger parsing RecentActivities shares.
    python -m src.activities.member_activities [--setup]    # --setup: every week since challenge_start
"""
import argparse
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import member_statistics
from ..config import settings
from ..members.members import CSV_PATH as MEMBERS_CSV
from ..strava_session import (CLUB_URL, ScrapeError, append_new_rows, club_page, csv_column_set,
                              read_csv, require_auth)

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


# What the club leaderboard counts: foot sports. Profiles also list rides, swims, workouts...
FOOT_TYPES = {"Run", "TrailRun", "VirtualRun", "Walk", "Hike"}

# Rank-table rows: the athlete's id comes from the profile link, the rest are cell texts.
ROWS_JS = """() => [...document.querySelectorAll('.leaderboard tbody tr')].map(tr => {
    const c = [...tr.children].map(td => td.innerText.trim());
    const a = tr.querySelector('a.athlete-name');
    return a && {id: a.getAttribute('href').split('/').pop(), name: a.innerText.trim(),
                 dist: c[2], acts: c[3], elev: c[4], time: c[5]};
}).filter(Boolean)"""

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


def weeks_to_sync(today: date, challenge_start: str, setup: bool) -> list:
    """Mondays to refresh: every week since challenge_start (setup), else last week and this week,
    so runs uploaded late into the week just closed still land."""
    this = monday_of(today)
    if setup:
        first = monday_of(date.fromisoformat(challenge_start))
        return [first + timedelta(weeks=i) for i in range((this - first).days // 7 + 1)]
    return [this - timedelta(weeks=1), this]   # ponytail: older weeks never re-checked; --setup repairs them


def parse_leaderboard(rows: list) -> dict:
    """Scraped rank-table rows -> {athlete_id: figures + name}."""
    return {r["id"]: {"name": r["name"],
                      "distance_m": to_meters(r["dist"]) or 0.0,
                      "moving_time_s": to_seconds(r["time"]) or 0,
                      "elev_gain_m": to_meters(r["elev"]) or 0.0,
                      "activities": to_int(r["acts"]) or 0}
            for r in rows}


def fetch_leaderboard() -> dict:
    """{monday_iso: {athlete_id: figures}} for this week and last week; the Last Week tab swaps the table in place."""
    this = monday_of(datetime.now(ZoneInfo(settings.timezone)).date())
    with club_page(f"{CLUB_URL}/leaderboard") as page:
        this_week = page.evaluate(ROWS_JS)
        page.locator("span.button.last-week").click()
        page.wait_for_timeout(1500)
        last_week = page.evaluate(ROWS_JS)
    if not this_week and not last_week:
        raise ScrapeError("Leaderboard empty - session expired or markup changed; re-run: python -m src.login")
    return {this.isoformat(): parse_leaderboard(this_week),
            (this - timedelta(weeks=1)).isoformat(): parse_leaderboard(last_week)}


def foot_rows(entries: list, athlete_id: str) -> list:
    """This athlete's own foot activities in a profile week's entries (group runs also list the others)."""
    rows = []
    for r in (r for e in entries for r in normalise(e)):
        r["activity_id"] = str(r["activity_id"] or "")
        if r["activity_id"] and str(r["athlete_id"]) == athlete_id and r["type"] in FOOT_TYPES:
            rows.append(r)
    return rows


def run(leaderboard: dict, setup: bool = False, workers: int = 4, only: list | None = None) -> int:
    """Scan every members.csv athlete's profile week(s) into the ledger and MemberStatistics; returns new ledger rows.
    `only`: just these athletes, every week since challenge_start (new members). Stops at Strava's first 429,
    keeping what it fetched."""
    require_auth()
    synced_at = now_utc()   # ledger rows found here share it, so the dashboard never adds them twice
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    mondays = weeks_to_sync(today, settings.challenge_start, setup or only is not None)
    members = only if only is not None else [r["athlete_id"] for r in read_csv(MEMBERS_CSV)]
    jobs = [(aid, m) for m in mondays for aid in members]
    print(f"weeks {', '.join(m.isoformat() for m in mondays)}: {len(jobs)} profile requests", flush=True)

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
                    raise ScrapeError(f"Session expired or blocked (HTTP {res['status']}) - re-run: python -m src.login")
                if "error" in res:
                    errors += 1
                    continue
                rows = foot_rows(res["entries"] or [], aid)  # None: private profile, nothing visible
                weeks[(aid, monday.isoformat())] = rows
                ledger += rows
            print(f"  [{b + len(batch)}/{len(jobs)}] errors {errors}", flush=True)
            if limited:
                break

    member_statistics.run(weeks, leaderboard, synced_at, today)
    new = append_activities(ledger, synced_at)
    print(f"{len(new)} new activities -> {CSV_PATH}")
    if limited:
        raise ScrapeError(f"Strava rate-limited (HTTP 429) after {len(weeks)} of {len(jobs)} profile requests; "
                          "kept what was fetched - the next full run resumes.")
    if errors > 0.02 * len(jobs):
        # What was found is saved; failing lets the scheduler flag the gaps.
        raise ScrapeError(f"{errors} of {len(jobs)} profile requests failed - re-run to retry.")
    return len(new)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # athlete names may be non-ASCII
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--setup", action="store_true", help="sync every week since challenge_start")
    run(fetch_leaderboard(), setup=parser.parse_args().setup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
