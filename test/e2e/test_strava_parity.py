"""Live end-to-end: the published dashboard's figures equal Strava's. Run right after a full pipeline run
(python -m src.main --full), since runs uploaded in between make honest mismatches:
    python -m pytest test/e2e/test_strava_parity.py --live
For every athlete on the club leaderboard (this week and last week, top 100 - all Strava shows), the
dashboard's cumulative snapshots must differ by exactly Strava's weekly distance, activities, elevation
and time; and its member total must be Strava's headline count."""
import json
import re
from datetime import date, timedelta

import pytest

from src import config
from src.activities.activities import fetch_leaderboard
from src.dashboard import generate
from src.members.members import fetch_count_and_feed

pytestmark = pytest.mark.live

# The dashboard rounds each cumulative figure (km to 0.1, elev to 1 m), so a weekly difference of two
# can be off by one rounding step; Strava's leaderboard rounds time to the minute.
TOL = {"km": 0.11, "elev": 1.01, "time_s": 60, "acts": 0}


def _embedded(html: str, name: str) -> dict:
    return json.loads(re.search(rf"const {name}\s*=\s*(.*?);\n", html).group(1))


@pytest.fixture(scope="module")
def dashboard():
    html = generate.OUT_PATH.read_text(encoding="utf-8")
    _, _, members, _, roll = generate.load(config.load())   # the same name resolution generate.run() used
    name_of = {m["athlete_id"]: roll.resolve(m["name"]) for m in members}
    return _embedded(html, "DATA"), _embedded(html, "DAILY"), name_of


def _figures(bucket: dict) -> dict:
    """{dashboard name: {km, elev, time_s, acts}} for one snapshot (slimmed zero rows count as zero)."""
    return {r["name"]: {k: r.get(k, 0) for k in TOL} for r in (bucket or {}).get("leaderboard", [])}


def _week_delta(after: dict, before: dict, name: str) -> dict:
    zero = dict.fromkeys(TOL, 0)
    a, b = after.get(name, zero), before.get(name, zero)
    return {k: a[k] - b[k] for k in TOL}


def test_dashboard_weeks_match_the_strava_leaderboard(dashboard):
    data, daily, name_of = dashboard
    board = fetch_leaderboard()
    this_monday = max(date.fromisoformat(w) for w in board)
    last_monday = this_monday - timedelta(weeks=1)

    def before(monday):   # the snapshot at the Sunday closing the previous week
        return _figures((daily.get((monday - timedelta(days=1)).isoformat()) or {}).get("all"))

    weeks = {   # Strava week -> (dashboard cumulative after it, before it)
        this_monday: (_figures(data["today"]["all"]), before(this_monday)),
        last_monday: (before(this_monday), before(last_monday)),
    }
    mismatches = []
    for monday, (after, before) in weeks.items():
        for aid, fig in board[monday.isoformat()].items():
            strava = {"km": fig["distance_m"] / 1000, "elev": fig["elev_gain_m"],
                      "time_s": fig["moving_time_s"], "acts": fig["activities"]}
            mine = _week_delta(after, before, name_of.get(aid, fig["name"]))
            if any(abs(mine[k] - strava[k]) > TOL[k] for k in TOL):
                mismatches.append(f"{monday} {aid} {fig['name']}: strava {strava} dashboard {mine}")
    assert not mismatches, f"{len(mismatches)} mismatches:\n" + "\n".join(mismatches)


def test_dashboard_member_total_is_stravas_headline_count(dashboard):
    data, _, _ = dashboard
    assert data["today"]["all"]["athlete_count"] == fetch_count_and_feed()[0]
