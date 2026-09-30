"""Find club members with no club-feed activity yet, from the nominal roll.

members.py grows the ledger only from activities.csv, so such members never get a row
and backfill.py never scans them. Each roll username not in the ledger is searched on
Strava (top few hits, display name must resemble the roll's username or Name) and appended
to members.csv if its public profile lists the club. Outcomes are remembered in
discover_state.json so a run only searches what is new or due for a re-check.

Daily, before the backfill (scripts/run_backfill.ps1); manual:

    python -m src.members.discover
"""
import csv
import difflib
import json
import random
import sys
import threading
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..dashboard.names import FUZZY_THRESHOLD, NominalRoll, _norm, _token_key
from ..strava_session import AUTH_PATH, CLUB_ID, ScrapeError, StravaScraper, append_new_rows, csv_column_set
from .members import CSV_PATH as MEMBERS_CSV, FIELDS

STATE_PATH = Path(__file__).parent / "discover_state.json"
RECHECK_DAYS = 7
TOP_HITS = 6  # search results considered per username
WORKERS = 4   # each worker drives its own browser (Playwright's sync API is single-threaded)

# What each outcome means, and what happens next; printed per lookup and in the summary.
STATUS_MEANING = {
    "member": f"one of the top {TOP_HITS} hits matches and its profile lists the club -> added to members.csv",
    "not_in_club": "public profile found, but it does not list the club -> re-check in %d days" % RECHECK_DAYS,
    "private": "profile is private, so its clubs can't be read -> re-check in %d days" % RECHECK_DAYS,
    "name_mismatch": f"search found someone, but none of the top {TOP_HITS} names resembles the roll's username/Name -> "
                     "skipped (likely a different person) until the roll row changes",
    "no_result": "search returned no athlete at all -> skipped until the roll row changes",
}
SEARCH_URL = "https://www.strava.com/athletes/search?query="
PROFILE_URL = "https://www.strava.com/athletes/"

# Only the result list: the page header also links "My Profile" (/athletes/<you>).
HITS_JS = r"""() => {
    const seen = new Set(), out = [];
    for (const a of document.querySelectorAll('[class*="AthleteList"] a[href*="/athletes/"]')) {
        const m = a.href.match(/\/athletes\/(\d+)$/);
        if (m && a.innerText.trim() && !seen.has(m[1])) { seen.add(m[1]); out.push([m[1], a.innerText.trim()]); }
    }
    return out;
}"""
CLUBS_JS = "() => [...document.querySelectorAll('a[href*=\"/clubs/\"]')].map(a => a.href)"


def name_ok(display: str, roll_name: str, username: str) -> bool:
    """True when a Strava display name resembles the roll's username or real Name
    (equal, same words in any order, one a word-subset of the other, or fuzzy >= FUZZY_THRESHOLD)."""
    d = _norm(display)
    for ref in (username, roll_name):
        n = _norm(ref)
        if not n:
            continue
        if d == n or _token_key(display) == _token_key(ref):
            return True
        dt, nt = set(d.split()), set(n.split())
        if len(dt) >= 2 and len(nt) >= 2 and (dt <= nt or nt <= dt):
            return True
        if difflib.SequenceMatcher(None, d, n).ratio() >= FUZZY_THRESHOLD:
            return True
    return False


def is_due(entry: dict | None, roll_name: str, now: datetime) -> bool:
    """Whether a roll username needs (re)searching given what state remembers about it."""
    if entry is None:
        return True
    if entry["status"] in ("not_in_club", "private"):  # can flip without the roll changing
        return now - datetime.fromisoformat(entry["checked_at"]) >= timedelta(days=RECHECK_DAYS)
    if entry["status"] in ("no_result", "name_mismatch"):
        return entry.get("roll_name") != roll_name
    return False   # member: already in the ledger


def candidates(roll: list, member_names: set, state: dict, now: datetime) -> list:
    """Roll rows with a username that is not a ledger name and is new or due for a re-check."""
    out = []
    for r in roll:
        u = r["STRAVA username"].strip()
        if u and _norm(u) not in member_names and is_due(state.get(u), r["Name"], now):
            out.append(r)
    return out


class DiscoverScraper(StravaScraper):
    """Search Strava for each due roll username and confirm club membership on the profile."""

    def lookup(self, page, row: dict) -> dict:
        """One roll row -> {status, athlete_id?, display?}: search, then check the top TOP_HITS
        results. A club member wins over a non-member; otherwise the first name match, else the top hit."""
        username = row["STRAVA username"].strip()
        page.goto(SEARCH_URL + urllib.parse.quote_plus(username), wait_until="networkidle")
        page.wait_for_timeout(1500)
        hits = page.evaluate(HITS_JS)[:TOP_HITS]
        if not hits:
            return {"status": "no_result"}
        results = []
        for athlete_id, display in hits:
            out = {"athlete_id": athlete_id, "display": display}
            if not name_ok(display, row["Name"], username):
                results.append({**out, "status": "name_mismatch"})
                continue
            page.goto(PROFILE_URL + athlete_id, wait_until="networkidle")
            page.wait_for_timeout(1200)
            if "This Account Is Private" in page.evaluate("() => document.body.innerText"):
                results.append({**out, "status": "private"})
                continue
            in_club = any(f"/clubs/{CLUB_ID}" in c for c in page.evaluate(CLUBS_JS))
            results.append({**out, "status": "member" if in_club else "not_in_club"})
            if in_club:
                break
        return next((r for r in results if r["status"] == "member"),
                    next((r for r in results if r["status"] != "name_mismatch"), results[0]))

    def _work(self, rows: list, progress: dict) -> list:
        """Look up `rows` in this worker's own browser; [(row, result)]. A failed lookup is
        logged and dropped (retried next run) so it can't lose the rest."""
        out = []
        with self._club_page() as page:
            for row in rows:
                username = row["STRAVA username"].strip()
                try:
                    res = self.lookup(page, row)
                except Exception as e:
                    res, err = None, type(e).__name__
                with progress["lock"]:
                    progress["done"] += 1
                    tag = f"[{progress['done']}/{progress['total']}]"
                    if res is None:
                        print(f"{tag} error         {username!r}: {err} -> will retry next run", flush=True)
                    else:
                        hit = f", chose {res['display']!r} (id {res['athlete_id']})" if "display" in res else ""
                        print(f"{tag} {res['status']:<13} {username!r} (roll: {row['Name']}){hit}"
                              f" | {STATUS_MEANING[res['status']]}", flush=True)
                if res:
                    out.append((row, res))
                page.wait_for_timeout(random.randint(800, 1800))
        return out

    def run(self) -> int:
        """Search due candidates in WORKERS parallel browsers; append confirmed members.
        Returns rows appended."""
        if not AUTH_PATH.exists():
            raise ScrapeError("No saved session. Run: python -m src.login")
        now = datetime.now(timezone.utc)
        with NominalRoll.CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
            roll = list(csv.DictReader(f))
        with MEMBERS_CSV.open(encoding="utf-8", newline="") as f:
            member_names = {_norm(r["name"]) for r in csv.DictReader(f)}
        state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
        todo = candidates(roll, member_names, state, now)
        workers = max(1, min(WORKERS, len(todo)))
        print(f"Discover from the nominal roll: {len(todo)} usernames to search, {workers} in parallel "
              f"({len(state)} already remembered, {len(roll)} on the roll)", flush=True)

        progress = {"done": 0, "total": len(todo), "lock": threading.Lock()}
        results = []
        if todo:
            with ThreadPoolExecutor(workers) as pool:
                for chunk in pool.map(lambda i: self._work(todo[i::workers], progress), range(workers)):
                    results += chunk

        seen = csv_column_set(MEMBERS_CSV, "athlete_id")
        new, stamp = [], now.isoformat(timespec="seconds")
        for row, res in results:
            state[row["STRAVA username"].strip()] = {"status": res["status"], "roll_name": row["Name"], "checked_at": stamp}
            if res["status"] == "member" and res["athlete_id"] not in seen:
                seen.add(res["athlete_id"])
                new.append({"athlete_id": res["athlete_id"], "name": res["display"], "first_seen": stamp})
        append_new_rows(MEMBERS_CSV, FIELDS, new)
        STATE_PATH.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

        counts = Counter(res["status"] for _, res in results)
        print(f"\n=== discover summary: {len(results)} of {len(todo)} looked up, {len(new)} new members -> {MEMBERS_CSV} ===")
        for status, meaning in STATUS_MEANING.items():
            print(f"  {status:<13} {counts[status]:>4}  {meaning}")
        return len(new)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # athlete names may be non-ASCII
    DiscoverScraper().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
