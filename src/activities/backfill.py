"""Backfill activities.csv from each member's Strava profile, not just the club feed.

The club feed only retains ~2.5 days (see activities.py), so a missed run window loses
activities for good. Every athlete profile has a "Monthly" interval chart; the XHR
behind clicking a month bar returns that month's activities as the same feed entries
the club feed carries, so normalise() parses them unchanged.

For every member in members.csv, for every month from challenge_start's month to now,
append public foot activities (the only kind the club feed admits) that started on/after
challenge_start and aren't in activities.csv yet, with entity "ProfileBackfill".
scraped_at is stamped now; the real start_date_utc is kept, so the dashboard counts
each on the day it actually happened.

One request per member per month (~1000 members). Requests run concurrently inside
one logged-in page (--workers at a time), with per-request retry/backoff on 429/5xx.
Manual only:

    python -m src.activities.backfill                # append missing activities
    python -m src.activities.backfill --dry-run      # only report them
    python -m src.activities.backfill --workers 24   # more concurrency (default 16)
"""
import argparse
import csv
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from ..config import settings
from ..dashboard.generate import _local_date
from ..members.members import CSV_PATH as MEMBERS_CSV
from ..strava_session import AUTH_PATH, ScrapeError, StravaScraper, append_new_rows, csv_column_set
from .activities import CSV_PATH, FIELDS, normalise

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
                if (!r.ok) return {error: 'HTTP ' + r.status, attempts: attempt + 1};
                return {entries: parse(await r.text()), attempts: attempt + 1};
            } catch (e) { last = String(e); }
        }
        return {error: last, attempts: 4};
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
    challenge_start, activity_id as str. Group runs can list other athletes too;
    those belong to their own profile pass. Followers-only activities can show up on a
    profile this account follows, but the club feed never carries them and the ledger
    feeds a public dashboard, so they are skipped."""
    rows = []
    for r in (r for e in entries for r in normalise(e)):
        r["activity_id"] = str(r["activity_id"] or "")
        if (r["activity_id"]
                and str(r["athlete_id"]) == athlete_id
                and r["type"] in FOOT_TYPES
                and r["visibility"] == "everyone"
                and _local_date(r["start_date_utc"], tzinfo) >= challenge_start):
            rows.append(r)
    return rows


def _members() -> dict:
    """members.csv -> {athlete_id: name}, in file order."""
    with MEMBERS_CSV.open(encoding="utf-8", newline="") as f:
        return {r["athlete_id"]: r["name"] for r in csv.DictReader(f)}


def _fmt_secs(s: float) -> str:
    m, s = divmod(int(s), 60)
    return f"{m}m{s:02d}s"


class BackfillScraper(StravaScraper):
    """Walk every member's monthly profile activities; append what the feed missed."""

    def run(self, dry_run: bool, workers: int) -> int:
        """Scan all members; append (unless dry_run) and return the missing-row count."""
        if not AUTH_PATH.exists():
            raise ScrapeError("No saved session. Run: python -m src.login")
        tzinfo = ZoneInfo(settings.timezone)
        months = months_since(settings.challenge_start, datetime.now(tzinfo).date())
        seen = csv_column_set(CSV_PATH, "activity_id")
        members = _members()
        jobs = [(aid, m) for aid in members for m in months]
        batch_size = workers * 6

        print(f"Backfill {'(DRY RUN) ' if dry_run else ''}from member profiles")
        print(f"  challenge start : {settings.challenge_start} ({settings.timezone})")
        print(f"  months          : {', '.join(months)}")
        print(f"  members         : {len(members)} (from {MEMBERS_CSV.name})")
        print(f"  requests        : {len(jobs)}, {workers} concurrent, batches of {batch_size}")
        print(f"  activities.csv  : {len(seen)} rows already", flush=True)

        found, hidden, errors = [], [], []
        per_member = {}  # athlete_id -> [on_profile, already_in_csv, missing]
        retried = 0
        t0 = time.monotonic()

        with self._club_page() as page:
            for b in range(0, len(jobs), batch_size):
                batch = jobs[b:b + batch_size]
                urls = [INTERVAL_URL.format(athlete_id=a, month=m) for a, m in batch]
                results = page.evaluate(BATCH_JS, {"urls": urls, "workers": workers})

                for (athlete_id, month), res in zip(batch, results):
                    name = members[athlete_id]
                    if res.get("expired"):
                        raise ScrapeError(f"Session expired or blocked (HTTP {res['status']}) at "
                                          f"{athlete_id} {month} - re-run: python -m src.login")
                    retried += res.get("attempts", 1) > 1
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
                      f"missing {len(found)}, hidden {len(hidden)}, errors {len(errors)}, "
                      f"retried {retried}", flush=True)

        self._summary(members, per_member, found, hidden, errors, time.monotonic() - t0)

        if found and not dry_run:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for r in found:
                r["scraped_at"] = now
                r["entity"] = "ProfileBackfill"  # tells these apart from club-feed rows
            append_new_rows(CSV_PATH, FIELDS, found)
            print(f"\nAppended {len(found)} rows -> {CSV_PATH}")
        elif found:
            print(f"\nDry run: {len(found)} rows NOT written.")
        return len(found)

    @staticmethod
    def _summary(members, per_member, found, hidden, errors, elapsed):
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
    parser.add_argument("--dry-run", action="store_true", help="report missing activities, don't write")
    parser.add_argument("--workers", type=int, default=16, help="concurrent profile requests (default 16)")
    args = parser.parse_args()
    BackfillScraper().run(args.dry_run, max(1, args.workers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
