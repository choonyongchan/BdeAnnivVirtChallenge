"""Members: the club's members page is the only source. Hourly it records the "1,097 members" headline in
member_count.csv and walks the paged roster (/members?page=1..N, 30 athletes a page, Admins then Members) into
members.csv: newcomers get ingest_at, athletes gone from the roster get left_at (cleared if they rejoin).
The headline is authoritative: when the roster does not tally with it (someone joined mid-walk, or a page
failed), newcomers are still added but nobody is marked as left.
"""
import html
import random
import re
from pathlib import Path

from shared.data import MEMBER_COUNT_CSV, MEMBERS_CSV, append_new_rows, now_utc, read_csv, write_csv
from ..strava_session import CLUB_URL, FETCH_JS, ScrapeError, club_page

CSV_PATH = MEMBERS_CSV
COUNT_PATH = MEMBER_COUNT_CSV
MEMBERS_URL = f"{CLUB_URL}/members"

FIELDS = ["athlete_id", "name", "ingest_at", "left_at"]
COUNT_FIELDS = ["scraped_at", "member_count"]
MAX_PAGES = 200   # circuit breaker: 6,000 members at 30 a page

ROSTER_ROW = re.compile(r"""class=['"]text-headline['"]>\s*<a href=['"]/athletes/(\d+)['"]>([^<]*)</a>""")


def parse_member_count(page_html: str) -> int | None:
    """The headline count from <span class='membership-count'>1055 members</span>, or None."""
    m = re.search(r"class=['\"]membership-count['\"][^>]*>\s*([\d,]+)\s+members?\b", page_html)
    return int(m.group(1).replace(",", "")) if m else None


def parse_roster(page_html: str) -> dict:
    """{athlete_id: name} of every athlete in the page's ul.list-athletes lists (Admins and Members)."""
    out = {}
    for block in re.split(r"""class=['"]list-athletes['"]""", page_html)[1:]:
        for aid, name in ROSTER_ROW.findall(block.split("</ul>")[0]):
            out[aid] = html.unescape(name).strip()
    return out


def fetch_page(page, n: int) -> str:
    """The members page n's HTML, fetched inside a logged-in club page."""
    result = page.evaluate(FETCH_JS, f"{MEMBERS_URL}?page={n}")
    if not result["ok"]:
        raise ScrapeError("Session expired or blocked - re-run: python -m backend.login\n"
                          f"Members page returned HTTP {result['status']}.")
    return result["text"]


def fetch_count_and_roster(page) -> tuple:
    """-> (headline count, {athlete_id: name}): pages 1.. until one lists nobody."""
    first = fetch_page(page, 1)
    count = parse_member_count(first)
    if count is None:
        raise ScrapeError("Member count not found on the members page - Strava markup may have changed.")
    roster = parse_roster(first)
    for n in range(2, MAX_PAGES + 1):
        page.wait_for_timeout(random.randint(400, 900))
        athletes = parse_roster(fetch_page(page, n))
        if not athletes or athletes.keys() <= roster.keys():   # past the end: no one new on this page
            break
        roster.update(athletes)
    return count, roster


def update_members(count: int, roster: dict, stamp: str) -> list:
    """Upsert members.csv from the roster; returns the newcomers' athlete ids.
    Tallied (roster size == headline): absent members get left_at, returning ones have it cleared."""
    rows = {r["athlete_id"]: r for r in read_csv(CSV_PATH)}
    tallied = len(roster) == count
    new = []
    for aid, name in roster.items():
        if aid not in rows:
            rows[aid] = {"athlete_id": aid, "name": name, "ingest_at": stamp, "left_at": ""}
            new.append(aid)
        elif tallied:
            rows[aid]["left_at"] = ""
    if tallied:
        for aid, r in rows.items():
            if aid not in roster and not r.get("left_at"):
                r["left_at"] = stamp
    else:
        print(f"WARNING: roster lists {len(roster)} athletes but the headline says {count}; "
              "newcomers added, nobody marked as left this run")
    write_csv(CSV_PATH, FIELDS, list(rows.values()))
    return new


def run() -> list:
    """Headline -> member_count.csv, roster -> members.csv; returns the newcomers' athlete ids."""
    with club_page(MEMBERS_URL) as page:
        count, roster = fetch_count_and_roster(page)
        if len(roster) != count:   # one retry: a join or leave mid-walk shifts the pages
            count, roster = fetch_count_and_roster(page)
    stamp = now_utc()
    append_new_rows(COUNT_PATH, COUNT_FIELDS, [{"scraped_at": stamp, "member_count": count}])
    new = update_members(count, roster, stamp)
    print(f"headline {count} members; roster {len(roster)}; {len(new)} new -> {CSV_PATH}")
    return new
