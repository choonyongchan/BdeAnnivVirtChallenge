"""
Generate a static index.html from current Strava club data.
Run locally or via GitHub Actions (hourly cron).

Usage:
  python3 src/generate.py
"""
import json, math
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from config import config
from strava_client import StravaClient
from nominal_roll import NominalRoll
import report_generator

# NSF group = these units. NSMen group = any roll unit outside this set.
NSF_UNITS = {"8SAB", "40SAR", "41SAR"}

# ---------------------------------------------------------------------------
# Weather — Open-Meteo (no API key needed)
# ---------------------------------------------------------------------------

WEATHER_CODES = {
    0: ("☀️", "Clear"), 1: ("🌤️", "Mostly clear"), 2: ("⛅", "Partly cloudy"),
    3: ("☁️", "Overcast"), 45: ("🌫️", "Fog"), 48: ("🌫️", "Rime fog"),
    51: ("🌦️", "Light drizzle"), 53: ("🌦️", "Drizzle"), 55: ("🌧️", "Heavy drizzle"),
    61: ("🌧️", "Light rain"), 63: ("🌧️", "Rain"), 65: ("🌧️", "Heavy rain"),
    71: ("🌨️", "Light snow"), 73: ("🌨️", "Snow"), 75: ("❄️", "Heavy snow"),
    80: ("🌦️", "Light showers"), 81: ("🌧️", "Showers"), 82: ("⛈️", "Heavy showers"),
    95: ("⛈️", "Thunderstorm"), 96: ("⛈️", "Thunderstorm w/ hail"), 99: ("⛈️", "Severe storm"),
}


def fetch_weather(config) -> dict:
    """Fetch current weather from Open-Meteo API."""
    try:
        import requests
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={config.weather_lat}&longitude={config.weather_lon}"
            "&current=temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m"
            f"&wind_speed_unit=kmh&timezone={config.timezone}"
        )
        r = requests.get(url, timeout=8)
        c = r.json()["current"]
        code = int(c.get("weather_code", 0))
        icon, desc = WEATHER_CODES.get(code, ("🌡️", ""))
        return {
            "icon": icon,
            "desc": desc,
            "temp": round(c.get("temperature_2m", 0)),
            "wind": round(c.get("wind_speed_10m", 0)),
            "ok": True,
        }
    except Exception as e:
        print(f"  Weather fetch failed: {e}")
        return {"ok": False}


# ---------------------------------------------------------------------------
# Timestamp in configured timezone
# ---------------------------------------------------------------------------

def now_tz(config) -> datetime:
    """Return current time in configured timezone, falling back to UTC."""
    try:
        import pytz
        return datetime.now(pytz.timezone(config.timezone))
    except Exception:
        return datetime.now(timezone.utc)


def now_label(config) -> tuple:
    """Return (iso_str, human_label) in configured timezone."""
    now = now_tz(config)
    iso = now.strftime("%Y-%m-%dT%H:%M")
    human = f"{now.day}.{now.month}.{now.year} {now.hour:02}:{now.minute:02}"
    return iso, human


def make_json_safe(stats: dict) -> dict:
    """Convert stats dict to JSON-serializable object (handle NaN/Inf)."""
    def fix(obj):
        if isinstance(obj, dict):
            return {k: fix(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [fix(i) for i in obj]
        if isinstance(obj, float):
            return 0.0 if math.isnan(obj) or math.isinf(obj) else obj
        return obj
    return fix(stats)


def build_grouped_data(acts: list, members: list, label: str, roll: NominalRoll = None) -> dict:
    """Split acts/members into 'all'/'nsf'/'nsmen' stats.

    nsf: roll members whose unit is in NSF_UNITS.
    nsmen: roll members whose unit is set but not in NSF_UNITS.
    Roll members with no unit on file land in neither group (still counted in 'all').
    """
    def unit_of(name):
        return roll.unit_company(name).get("unit", "") if roll else ""

    def in_roll(name):
        return roll.is_member(name) if roll else False

    def act_name(a):
        ath = a.get("athlete", {})
        raw = NominalRoll.full_name(ath, default_firstname="?")
        return roll.resolve(raw) if roll else raw

    def member_name(m):
        raw = NominalRoll.full_name(m)
        return roll.resolve(raw) if roll else raw

    def stats_for(filtered_acts, filtered_members):
        s = report_generator.compute_stats(filtered_acts, members=filtered_members, roll=roll)
        s["label"] = label
        s["count"] = len(filtered_acts)
        return make_json_safe(s)

    result = {"all": stats_for(acts, members)}
    result["nsf"] = stats_for(
        [a for a in acts if unit_of(act_name(a)) in NSF_UNITS],
        [m for m in (members or []) if unit_of(member_name(m)) in NSF_UNITS],
    )
    result["nsmen"] = stats_for(
        [a for a in acts if in_roll(act_name(a)) and unit_of(act_name(a)) not in NSF_UNITS],
        [m for m in (members or []) if in_roll(member_name(m)) and unit_of(member_name(m)) not in NSF_UNITS],
    )
    return result


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__CLUB_NAME__ – Strava Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏃</text></svg>">

<meta property="og:title"       content="__CLUB_NAME__ – Weekly Strava Report">
<meta property="og:description" content="Live running stats for __CLUB_NAME__ from Strava.">
<meta property="og:type"        content="website">

<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Inter', -apple-system, sans-serif;
  background: #f0f2f5;
  min-height: 100vh;
  color: #1c1c1e;
}
a { color: inherit; text-decoration: none; }

/* NAV */
nav {
  background: white;
  border-bottom: 1px solid #eee;
  padding: 0 20px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 10;
  box-shadow: 0 1px 3px rgba(0,0,0,.06);
}
.nav-logo { display: flex; align-items: center; gap: 10px; }
.nav-badge {
  background: #FC4C02; color: white;
  font-weight: 800; font-size: 1rem;
  padding: 3px 8px; border-radius: 4px;
  letter-spacing: -.5px;
}
.nav-title { font-weight: 700; font-size: .9rem; color: #555; }
.nav-link {
  background: #FC4C02; color: white;
  padding: 6px 14px; border-radius: 6px;
  font-size: .78rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .05em;
  transition: background .15s;
}
.nav-link:hover { background: #e04400; }

/* WRAP */
.wrap { max-width: 1120px; margin: 0 auto; padding: 28px 20px 48px; }

/* ANNOUNCEMENT BANNER */
.announcement {
  background: white; border-left: 4px solid #FC4C02;
  border-radius: 10px; padding: 12px 40px 12px 16px;
  box-shadow: 0 1px 4px rgba(0,0,0,.07);
  margin-bottom: 18px; position: relative;
}
.announcement-title { font-weight: 700; font-size: .88rem; margin-bottom: 3px; }
.announcement-body { font-size: .82rem; color: #666; }
.announcement-close {
  position: absolute; top: 8px; right: 8px;
  background: none; border: none; cursor: pointer;
  font-size: 1.1rem; color: #bbb; padding: 4px 8px;
  border-radius: 6px; line-height: 1;
}
.announcement-close:hover { background: #f5f5f5; color: #555; }

/* HEADER */
.header { margin-bottom: 20px; }
.header-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.header h1 { font-size: 1.5rem; font-weight: 800; }
.header .sub {
  color: #888; font-size: .82rem; margin-top: 8px;
  display: flex; align-items: center; gap: 6px;
}
.dot { width: 7px; height: 7px; border-radius: 50%; background: #FC4C02; }
.weather-widget {
  background: white; border-radius: 10px;
  padding: 8px 14px; font-size: .82rem; color: #555;
  box-shadow: 0 1px 4px rgba(0,0,0,.07);
  white-space: nowrap; flex-shrink: 0;
  display: flex; align-items: center; gap: 6px;
}

/* GROUP TABS */
.group-tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.group-tab {
  padding: 7px 14px; border-radius: 8px;
  font-size: .82rem; font-weight: 600;
  border: 1.5px solid #ddd; background: white;
  color: #666; cursor: pointer; transition: all .15s;
  white-space: nowrap;
}
.group-tab.active { background: #FC4C02; color: white; border-color: #FC4C02; }
.group-tab:not(.active):hover { border-color: #bbb; color: #333; }
.breadcrumb { font-size: .78rem; color: #999; margin-bottom: 10px; }
.breadcrumb .crumb-group { font-weight: 700; color: #FC4C02; }
.breadcrumb .crumb-sub { font-weight: 600; color: #666; }

/* CONTROLS ROW */
.controls-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; margin-bottom: 18px; flex-wrap: wrap;
}
.toggle { display: flex; gap: 6px; flex-wrap: wrap; }
.tab {
  padding: 7px 14px; border-radius: 8px;
  font-size: .82rem; font-weight: 600;
  border: 1.5px solid #ddd; background: white;
  color: #666; cursor: pointer; transition: all .15s;
  white-space: nowrap;
}
.tab.active { background: #FC4C02; color: white; border-color: #FC4C02; }
.tab:not(.active):hover { border-color: #bbb; color: #333; }


/* TOTALS */
.totals { display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin-bottom: 22px; }
.total-card {
  background: white; border-radius: 12px;
  padding: 16px 10px; text-align: center;
  box-shadow: 0 1px 4px rgba(0,0,0,.07);
}
.total-card .val { font-size: 1.55rem; font-weight: 800; color: #FC4C02; }
.total-card .lbl { font-size: .75rem; color: #777; margin-top: 3px; }

/* HISTORY — modal */
.history-wrap { position: relative; display: inline-block; }
.history-picker {
  display: none; position: fixed;
  top: 50%; left: 50%; transform: translate(-50%, -50%);
  background: white; border-radius: 18px;
  box-shadow: 0 16px 60px rgba(0,0,0,.25);
  padding: 20px; z-index: 200;
  width: 340px; max-width: calc(100vw - 32px);
  max-height: 80vh; overflow-y: auto;
}
.history-picker.open { display: block; }
.history-picker-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 14px;
}
.history-picker-title {
  font-size: .7rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: .08em; color: #999; margin: 0;
}
.history-picker-close {
  background: none; border: none; cursor: pointer;
  font-size: 1.1rem; color: #bbb; padding: 2px 6px;
  border-radius: 6px; line-height: 1;
}
.history-picker-close:hover { background: #f5f5f5; color: #555; }
.hist-year-label {
  font-size: .72rem; font-weight: 700; color: #555;
  margin: 10px 0 6px; letter-spacing: .04em;
}
.hist-year-label:first-of-type { margin-top: 0; }
.hist-grid {
  display: grid; grid-template-columns: repeat(8, 1fr); gap: 4px;
}
.hist-cal-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 8px;
}
.hist-cal-month { font-size: .8rem; font-weight: 700; color: #333; }
.hist-cal-nav {
  background: none; border: none; cursor: pointer;
  font-size: 1rem; font-weight: 700; color: #777; padding: 2px 10px;
  border-radius: 6px; line-height: 1.4;
}
.hist-cal-nav:hover:not(:disabled) { background: #f5f5f5; color: #333; }
.hist-cal-nav:disabled { color: #ddd; cursor: default; }
.hist-cal-weekdays {
  display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px;
  margin-bottom: 4px;
}
.hist-cal-weekdays div {
  text-align: center; font-size: .62rem; font-weight: 700;
  color: #bbb; text-transform: uppercase;
}
.hist-cal-grid { grid-template-columns: repeat(7, 1fr); margin-bottom: 14px; }
.hist-cell {
  aspect-ratio: 1; border-radius: 6px; border: none;
  font-size: .7rem; font-weight: 700; cursor: default;
  display: flex; align-items: center; justify-content: center;
  transition: all .15s;
}
.hist-cell.has-data {
  background: #fff0eb; color: #FC4C02;
  cursor: pointer; border: 1.5px solid #ffd6c8;
}
.hist-cell.has-data:hover { background: #FC4C02; color: white; border-color: #FC4C02; }
.hist-cell.active { background: #FC4C02 !important; color: white !important; border-color: #FC4C02 !important; }
.hist-cell.active:hover { background: #e04300 !important; border-color: #e04300 !important; }
.hist-cell.current { background: #f5f5f5; color: #bbb; cursor: pointer; }
.hist-cell.current:hover { background: #eaeaea; color: #aaa; }
.hist-cell.empty { color: #e0e0e0; }

/* SECTION TITLE */
.section-title {
  font-size: .75rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .08em;
  color: #999; margin-bottom: 10px;
}

/* AWARDS */
.awards { display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin-bottom: 24px; }
@media(max-width:800px){ .awards { grid-template-columns: repeat(2,1fr); } }
@media(max-width:500px){ .awards { grid-template-columns: 1fr; } }
.award-card {
  background: white; border-radius: 12px;
  padding: 14px 16px;
  box-shadow: 0 1px 4px rgba(0,0,0,.07);
  display: flex; align-items: center; gap: 12px;
  transition: box-shadow .15s;
}
.award-card:hover { box-shadow: 0 3px 12px rgba(0,0,0,.1); }
.award-emoji { font-size: 1.6rem; flex-shrink: 0; }
.award-title { font-size: .72rem; color: #888; text-transform: uppercase; letter-spacing: .06em; }
.award-name { font-weight: 700; font-size: .95rem; margin: 1px 0; }
.award-val { font-size: .83rem; font-weight: 600; color: #FC4C02; }
.award-val.muted { color: #888; }

/* FUN STATS */
.fun-cards { display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; margin-bottom: 24px; }
@media(max-width:700px){ .fun-cards { grid-template-columns: 1fr; } }
.fun-card {
  background: #fffbf0; border: 1.5px solid #ffe8b0;
  border-radius: 12px; padding: 13px 16px;
  display: flex; align-items: center; gap: 12px;
}
.fun-emoji { font-size: 1.4rem; flex-shrink: 0; }
.fun-title { font-size: .72rem; color: #b07800; text-transform: uppercase; letter-spacing: .06em; }
.fun-name { font-weight: 700; font-size: .95rem; margin: 1px 0; }
.fun-val { font-size: .83rem; color: #666; }
.fun-desc { font-size: .78rem; color: #a07020; font-style: italic; margin-top: 3px; }

/* TABLE */
.table-wrap {
  background: white; border-radius: 12px;
  box-shadow: 0 1px 4px rgba(0,0,0,.07);
  margin-bottom: 24px;
  overflow: hidden;
}
table { width: 100%; border-collapse: collapse; font-size: .87rem; }
thead th {
  background: #fafafa; color: #777;
  font-size: .72rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .06em;
  padding: 11px 16px; text-align: right;
  border-bottom: 1px solid #f0f0f0;
  white-space: nowrap;
}
thead th:first-child { text-align: center; }
thead th:nth-child(2) { text-align: left; }
tbody tr:not(:last-child) { border-bottom: 1px solid #f7f7f7; }
tbody tr:hover { background: #fafafa; }
tbody td { padding: 12px 16px; text-align: right; color: #555; white-space: nowrap; }
tbody td:first-child { text-align: center; width: 40px; color: #999; }
thead th:nth-child(10), thead th:nth-child(11), thead th:nth-child(12) { text-align: center; }
tbody td:nth-child(10), tbody td:nth-child(11), tbody td:nth-child(12) { text-align: center; }
tbody td:nth-child(2) { text-align: left; font-weight: 700; color: #1c1c1e; }
tbody td:nth-child(3), tbody td:nth-child(4) { text-align: left; font-size: .8rem; color: #777; }
.km-cell { font-weight: 800; color: #FC4C02; }
.gap-cell { font-size: .78rem; color: #999; }
.gap-cell.leader { color: #FC4C02; font-weight: 700; font-size: .82rem; }
thead th.sortable { cursor: pointer; user-select: none; }
thead th.sortable:hover { background: #f0f0f0; color: #444; }
thead th.sortable::after { content: ' ↓'; opacity: 0; }
thead th.sortable:hover::after { opacity: 0.3; }
thead th.sort-asc::after  { content: ' ↑'; opacity: 1 !important; color: #FC4C02; }
thead th.sort-desc::after { content: ' ↓'; opacity: 1 !important; color: #FC4C02; }

/* Mobile table — scroll + sticky first two columns */
@media(max-width:700px) {
  .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; overflow-y: visible; }
  table { width: max-content; min-width: 100%; }
  thead th:first-child {
    position: sticky; left: 0; z-index: 3; background: #fafafa;
  }
  thead th:nth-child(2) {
    position: sticky; left: 40px; z-index: 3; background: #fafafa;
    box-shadow: 3px 0 8px rgba(0,0,0,.07);
  }
  tbody td:first-child {
    position: sticky; left: 0; background: white; z-index: 1;
  }
  tbody td:nth-child(2) {
    position: sticky; left: 40px; background: white; z-index: 1;
    box-shadow: 3px 0 8px rgba(0,0,0,.07);
    min-width: 110px;
  }
  tbody tr:hover td:first-child,
  tbody tr:hover td:nth-child(2) { background: #fafafa; }
  thead th, tbody td { padding: 11px 12px; font-size: .82rem; }
}

/* HISTORY — backdrop */
.hist-backdrop {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,.35);
  backdrop-filter: blur(2px); -webkit-backdrop-filter: blur(2px);
  z-index: 199;
}
.hist-backdrop.open { display: block; }
@media (max-width: 640px) {
  .hist-grid { grid-template-columns: repeat(9, 1fr); gap: 5px; }
  .hist-cell { font-size: .68rem; }
}

/* DEVICES */
.dev-chip {
  background: white; border-radius: 8px; padding: 7px 14px;
  box-shadow: 0 1px 4px rgba(0,0,0,.07);
  font-size: .82rem; display: flex; align-items: center; gap: 6px;
}
@media (min-width: 701px) {
  .dev-chip.dev-hidden { display: flex !important; }
  .dev-more-btn { display: none !important; }
}
.dev-chip.dev-hidden { display: none; }
.dev-chip.dev-hidden.dev-visible { display: flex; }
.dev-more-btn {
  background: none; border: 1.5px solid #ccc;
  border-radius: 8px; padding: 6px 14px;
  font-size: .8rem; color: #555; cursor: pointer;
  white-space: nowrap;
}
.dev-more-btn:hover { border-color: #999; color: #333; }

/* COLUMN FILTER MENU */
.th-filterable { cursor: pointer; user-select: none; }
.th-filterable:hover { background: #f0f0f0 !important; color: #444 !important; }
.th-filterable.filter-active { color: #FC4C02 !important; }
.filter-menu {
  position: fixed; background: white; border-radius: 10px;
  box-shadow: 0 6px 24px rgba(0,0,0,.14); border: 1px solid #eee;
  z-index: 100; min-width: 150px; padding: 4px 0; font-size: .83rem;
  max-height: 280px; overflow-y: auto;
}
.filter-menu-item {
  padding: 8px 16px; cursor: pointer; color: #444; white-space: nowrap;
}
.filter-menu-item:hover { background: #f5f5f5; }
.filter-menu-item.active { color: #FC4C02; font-weight: 700; }

/* GROUP RANKINGS */
.group-rankings { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
@media(max-width:600px){ .group-rankings { grid-template-columns: 1fr; } }

/* TREND */
.trend-toggle { display: flex; gap: 6px; flex-wrap: wrap; }
.trend-charts-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
.trend-charts-row.triple { grid-template-columns: repeat(3,1fr); }
@media(max-width:800px){ .trend-charts-row, .trend-charts-row.triple { grid-template-columns: 1fr; } }
.chart-card {
  background: white; border-radius: 12px;
  padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,.07);
}
.chart-card .chart-title { font-size: .78rem; font-weight: 700; color: #555; margin-bottom: 10px; }
.line-chart-svg { width: 100%; height: auto; display: block; overflow: visible; }
.line-chart-legend { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 10px; font-size: .74rem; color: #666; }
.line-chart-legend .lg-item { display: flex; align-items: center; gap: 5px; }
.line-chart-legend .lg-swatch { width: 14px; height: 2px; border-radius: 1px; display: inline-block; }
.line-chart-legend .lg-swatch.dashed { background: none !important; border-top: 2px dashed; height: 0; }
.line-chart-tooltip {
  position: fixed; background: #1c1c1e; color: white;
  border-radius: 8px; padding: 8px 12px; font-size: .74rem;
  pointer-events: none; z-index: 300; display: none;
  box-shadow: 0 4px 16px rgba(0,0,0,.25); white-space: nowrap;
}
.line-chart-tooltip .lct-date { font-weight: 700; margin-bottom: 4px; color: #ccc; }
.line-chart-tooltip .lct-row { display: flex; align-items: center; gap: 6px; }
.line-chart-tooltip .lct-swatch { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.line-chart-tooltip .lct-val { font-weight: 700; margin-left: 14px; }
.line-chart-empty { text-align: center; color: #ccc; padding: 40px 0; font-size: .82rem; }

/* FOOTER */
.footer { text-align: center; color: #aaa; font-size: .75rem; margin-top: 8px; }
.footer a { color: #FC4C02; }
.footer a:hover { text-decoration: underline; }

/* EMPTY STATE */
.empty-state {
  background: white; border-radius: 16px;
  padding: 64px 40px 56px;
  box-shadow: 0 1px 4px rgba(0,0,0,.07);
  margin-bottom: 24px;
  text-align: center;
}
.empty-state h2 {
  font-size: 1.5rem; font-weight: 800; color: #1c1c1e;
  display: inline-flex; align-items: center; gap: 11px;
  margin-bottom: 14px;
}
.empty-state h2 .es-emoji { font-size: 1.9rem; line-height: 1; }
.empty-state p {
  font-size: .92rem; color: #888;
  max-width: 420px; margin: 0 auto;
  line-height: 1.75;
}
.empty-state .es-divider {
  width: 36px; height: 2px; background: #efefef;
  margin: 26px auto; border-radius: 2px;
}
.empty-state .es-action { font-size: .84rem; color: #aaa; }
.empty-state .es-action a { color: #FC4C02; font-weight: 600; cursor: pointer; }
.empty-state .es-action a:hover { text-decoration: underline; }
.empty-state .es-hint { font-size: .82rem; color: #aaa; margin-top: 10px; }

/* FADE */
.fade { animation: fadeUp .35s ease; }
@keyframes fadeUp { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:none} }

@media(max-width:700px){
  .header-top { flex-direction: column; align-items: flex-start; gap: 6px; }
  .header h1 { font-size: 1.35rem; }
  .weather-widget { display: none; }
  .controls-row { flex-direction: column; align-items: flex-start; gap: 8px; }
  .nav-title { display: none; }
}

@media(max-width:600px){
  .totals { grid-template-columns: repeat(2,1fr); }
  .total-card { padding: 12px 8px; }
  .total-card .val { font-size: 1.25rem; }
}
</style>
</head>
<body>

<nav>
  <div class="nav-logo">
    <span class="nav-badge">__CLUB_SHORT__</span>
    <span class="nav-title">__CLUB_NAME__</span>
  </div>
  <a href="https://www.strava.com/clubs/__CLUB_ID__" target="_blank" class="nav-link">Strava Club</a>
</nav>

<div class="wrap">
  __ANNOUNCEMENT__
  <div class="header">
    <div class="header-top">
      <div>
        <h1>🏃__CLUB_NAME__</h1>
        <div class="sub">
          <span class="dot"></span>
          <span id="period-label"></span>
        </div>
      </div>
      __WEATHER__
    </div>
  </div>

  <div class="group-tabs" id="group-tabs">
    <button class="group-tab active" onclick="setGroup('all', this)">All</button>
    <button class="group-tab" onclick="setGroup('nsf', this)">NSF</button>
    <button class="group-tab" onclick="setGroup('nsmen', this)">NSMen</button>
  </div>
  <div class="breadcrumb" id="breadcrumb"></div>

  <div class="controls-row">
    <div class="toggle">
      <button class="tab active" onclick="showLeaderboard()" id="btn-leaderboard">🏆 Leaderboard</button>
      <button class="tab" onclick="showPrevWeek()" id="btn-7days" style="display:none">Last Week</button>
      <div class="history-wrap" id="history-wrap" style="display:none">
        <button class="tab" onclick="toggleHistoryPicker(event)" id="btn-history">📅 History</button>
        <div class="history-picker" id="history-picker">
          <div class="history-picker-header">
            <div class="history-picker-title">Pick a date</div>
            <button class="history-picker-close" onclick="closeHistoryPicker()" title="Close">✕</button>
          </div>
          <div id="history-picker-list"></div>
        </div>
      </div>
      <button class="tab" onclick="showTrend()" id="btn-trend">📈 Trend</button>
    </div>
  </div>
  <div class="hist-backdrop" id="hist-backdrop" onclick="closeHistoryPicker()"></div>

  <div id="empty-state" class="empty-state" style="display:none"></div>

  <div id="totals-section">
    <div class="totals" id="totals"></div>
  </div>

  <div id="awards-section">
    <div class="section-title">Awards</div>
    <div class="awards" id="awards"></div>
  </div>

  <div id="fun-section" style="display:none">
    <div class="section-title">Fun Stats 😄</div>
    <div class="fun-cards" id="fun-stats"></div>
  </div>

  <div id="device-section" style="display:none;margin-bottom:24px">
    <div class="section-title">Devices in the Club</div>
    <div id="devices" style="display:flex;gap:8px;flex-wrap:wrap"></div>
  </div>

  <div id="registration-section">
    <div class="section-title">Registration</div>
    <div class="table-wrap" id="registration-tree"></div>
  </div>

  <div id="group-rankings-section" class="group-rankings">
    <div>
      <div class="section-title">Unit Rankings</div>
      <div class="table-wrap" id="unit-rankings"></div>
    </div>
    <div>
      <div class="section-title">Company Rankings</div>
      <div class="table-wrap" id="company-rankings"></div>
    </div>
  </div>

  <div id="leaderboard-section">
    <div class="section-title">Runner Leaderboard <span id="leaderboard-count" style="text-transform:none;letter-spacing:normal;color:#bbb;font-weight:400"></span></div>
    <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Runner</th>
          <th class="th-filterable" id="th-unit"    onclick="toggleFilterMenu('unit', this)">Unit ▾</th>
          <th class="th-filterable" id="th-company" onclick="toggleFilterMenu('company', this)">Company ▾</th>
          <th class="sortable sort-desc" data-key="km"           onclick="sortBy('km')">km</th>
          <th>Gap</th>
          <th class="sortable" data-key="elev"          onclick="sortBy('elev')">Elevation</th>
          <th class="sortable" data-key="elev_per_km"   onclick="sortBy('elev_per_km')">m+/km</th>
          <th class="sortable" data-key="time_s"        onclick="sortBy('time_s')">Time</th>
          <th class="sortable" data-key="acts"          onclick="sortBy('acts')">Runs</th>
          <th class="sortable" data-key="avg_speed_ms"  onclick="sortBy('avg_speed_ms')">Avg Speed</th>
          <th class="sortable" data-key="longest"       onclick="sortBy('longest')">Longest</th>
        </tr>
      </thead>
      <tbody id="leaderboard"></tbody>
    </table>
  </div>
  </div>

  <div id="trend-section" style="display:none">
    <div class="chart-card" style="margin-bottom:24px">
      <div class="chart-title">Weekly Snapshot (Sundays)</div>
      <div class="table-wrap">
        <table id="trend-weekly-table">
          <thead>
            <tr>
              <th style="text-align:left">Week Of</th>
              <th>Total KM</th>
              <th>Activities</th>
              <th>Active Runners</th>
              <th>Registered</th>
              <th>Participation %</th>
              <th>Avg KM/Active</th>
              <th>Elevation (m)</th>
            </tr>
          </thead>
          <tbody id="trend-weekly-body"></tbody>
        </table>
      </div>
    </div>

    <div class="trend-charts-row">
      <div class="chart-card">
        <div class="chart-title">Cumulative Distance (km)</div>
        <div id="trend-distance-chart"></div>
      </div>
      <div class="chart-card">
        <div class="chart-title">Cumulative Activities</div>
        <div id="trend-activities-chart"></div>
      </div>
    </div>

    <div style="display:flex;align-items:center;justify-content:flex-end;margin-bottom:10px">
      <div class="trend-toggle" id="trend-runner-toggle" style="margin-bottom:0">
        <button class="tab active" onclick="setTrendRunnerView('all', this)">All</button>
        <button class="tab" onclick="setTrendRunnerView('nsf-nsmen', this)">NSF / NSMen</button>
        <button class="tab" onclick="setTrendRunnerView('unit', this)">Unit</button>
        <button class="tab" onclick="setTrendRunnerView('company', this)">Company</button>
      </div>
    </div>
    <div class="trend-charts-row" style="margin-bottom:24px">
      <div class="chart-card">
        <div class="chart-title">Active Runners</div>
        <div id="trend-active-runners-chart"></div>
      </div>
      <div class="chart-card">
        <div class="chart-title">Registered Runners</div>
        <div id="trend-registered-runners-chart"></div>
      </div>
    </div>

    <div class="trend-charts-row triple">
      <div class="chart-card">
        <div class="chart-title">Participation Rate (%)</div>
        <div id="trend-participation-chart"></div>
      </div>
      <div class="chart-card">
        <div class="chart-title">Avg km / Active Runner</div>
        <div id="trend-avgkm-chart"></div>
      </div>
      <div class="chart-card">
        <div class="chart-title">Cumulative Elevation (m)</div>
        <div id="trend-elevation-chart"></div>
      </div>
    </div>
  </div>
  <div class="line-chart-tooltip" id="line-chart-tooltip"></div>

  <div class="footer">
    Updated hourly &nbsp;·&nbsp; last update __UPDATED_HUMAN__ &nbsp;·&nbsp;
    <a href="https://www.strava.com/clubs/__CLUB_ID__" target="_blank">Strava Club</a>
    &nbsp;·&nbsp;
    <a href="https://github.com/DatabenderSK/strava-club-dashboard" target="_blank">Get your own dashboard</a>
  </div>
</div>

<script>
const DATA = __DATA__;
const DAILY   = __DAILY_DATA__;
const MEDALS = ['🥇','🥈','🥉'];
const AWARDS = [
  { key:'king_km',      emoji:'👑', title:'Distance King',   muted:false },
  { key:'king_elev',    emoji:'🏔️', title:'Climbing King',   muted:false },
  { key:'marathoner',   emoji:'⏱️', title:'Marathoner',       muted:false },
  { key:'fastest',      emoji:'⚡', title:'Fastest',          muted:false },
  { key:'longest',      emoji:'📏', title:'Longest Run',      muted:false },
  { key:'climber',      emoji:'🐐', title:'Mountain Goat',    muted:false },
  { key:'flatrunner',   emoji:'🛣️', title:'Flat Runner',      muted:false },
];
const FUN = [
  { key:'breaks',   emoji:'🛋️', title:'Break King',       desc:"Coffee breaks don't take themselves." },
];
// Categorical palette, fixed order — never cycled/reassigned by rank.
const LINE_COLORS = ['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#4a3aa7','#e34948'];

const EMPTY_MSGS = [
  { emoji: '🛋️', title: 'Runners are in offline mode this week', body: 'No one has logged a run yet. Shoes are resting.' },
  { emoji: '🦗', title: 'Quiet as a Monday morning track', body: "Nobody's laced up this week yet. Roads are waiting patiently." },
  { emoji: '⏳', title: 'Week is just getting started', body: "Nothing to measure or count yet. Maybe tomorrow." },
  { emoji: '🌧️', title: 'Rain? Wind? Comfy couch?', body: 'Reason unknown, result clear — no activities this week yet.' },
  { emoji: '🧘', title: 'Recovery week', body: "At least that's what the support crew says. Either way — no runs yet." },
];

function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

class Dashboard {
  constructor() {
    this.currentSort      = { key: 'km', dir: -1 };
    this.currentDailyDate = null;
    this.calendarMonth    = null; // 'YYYY-MM', set on first render
    this.filterUnit       = '';
    this.filterCompany    = '';
    this.currentGroup     = 'all';
    this.currentSub       = 'leaderboard';
    this.openFilterKey    = null;
    this.trendRunnerView  = 'all';
  }

  currentBucket() {
    return this.currentDailyDate ? DAILY[this.currentDailyDate] : DATA['today'];
  }

  data() {
    const bucket = this.currentBucket();
    return bucket[this.currentGroup] || bucket['all'];
  }

  setGroup(g) {
    this.currentGroup = g;
  }

  setSub(sub) {
    this.currentSub = sub;
  }

  sortBy(key) {
    if (this.currentSort.key === key) {
      this.currentSort.dir *= -1;
    } else {
      this.currentSort = { key, dir: -1 };
    }
  }

  sortedLeaderboard(rows) {
    const { key, dir } = this.currentSort;
    return [...rows].sort((a, b) => {
      const va = a[key] ?? 0;
      const vb = b[key] ?? 0;
      return (va < vb ? -1 : va > vb ? 1 : 0) * dir;
    });
  }

  changeCalendarMonth(delta) {
    const [y, m] = this.calendarMonth.split('-').map(Number);
    const nd = new Date(y, m - 1 + delta, 1);
    this.calendarMonth = `${nd.getFullYear()}-${String(nd.getMonth() + 1).padStart(2, '0')}`;
  }

  ensureCalendarMonth(dailyKeys) {
    if (!this.calendarMonth) {
      this.calendarMonth = (dailyKeys.length ? dailyKeys.sort().at(-1) : new Date().toISOString()).slice(0, 7);
    }
  }

  setFilter(key, value) {
    if (key === 'unit') this.filterUnit = value;
    else this.filterCompany = value;
  }

  resetInvalidFilters(units, companies) {
    if (this.filterUnit    && !units.has(this.filterUnit))        this.filterUnit    = '';
    if (this.filterCompany && !companies.has(this.filterCompany)) this.filterCompany = '';
  }

  showLeaderboard() {
    this.currentDailyDate = null;
  }

  showDailySnapshot(date) {
    this.currentDailyDate = date;
  }
}

const dashboard = new Dashboard();

const GROUP_LABELS = { all: 'All', nsf: 'NSF', nsmen: 'NSMen' };
const SUB_LABELS    = { leaderboard: 'Leaderboard', '7days': 'Last Week', history: 'History', trend: 'Trend' };

function fmtTime(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return `${h}h ${m < 10 ? '0' : ''}${m}m`;
}

function currentBucket() {
  return dashboard.currentBucket();
}

function d() {
  return dashboard.data();
}

function setGroup(g, el) {
  dashboard.setGroup(g);
  document.querySelectorAll('.group-tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  if (dashboard.currentSub === 'trend') renderTrend(); else render();
  updateBreadcrumb(dashboard.currentSub);
}

function updateBreadcrumb(sub) {
  dashboard.setSub(sub);
  const bucket = dashboard.currentBucket();
  const available = !!bucket[dashboard.currentGroup];
  const note = (dashboard.currentGroup !== 'all' && !available) ? ' <span style="color:#bbb">(group breakdown unavailable for this date — showing All)</span>' : '';
  document.getElementById('breadcrumb').innerHTML =
    `<span class="crumb-group">${GROUP_LABELS[dashboard.currentGroup]}</span> › <span class="crumb-sub">${SUB_LABELS[sub]}</span>${note}`;
}

function toggleHistoryPicker(e) {
  e.stopPropagation();
  const open = document.getElementById('history-picker').classList.toggle('open');
  document.getElementById('hist-backdrop').classList.toggle('open', open);
}

function closeHistoryPicker() {
  document.getElementById('history-picker').classList.remove('open');
  document.getElementById('hist-backdrop').classList.remove('open');
}

document.addEventListener('click', function(e) {
  if (!document.getElementById('history-wrap').contains(e.target)) closeHistoryPicker();
});

function sortBy(key) {
  dashboard.sortBy(key);
  document.querySelectorAll('thead th.sortable').forEach(th => {
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.key === key) {
      th.classList.add(dashboard.currentSort.dir === -1 ? 'sort-desc' : 'sort-asc');
    }
  });
  renderLeaderboard(d());
}

function sortedLeaderboard(rows) {
  return dashboard.sortedLeaderboard(rows);
}

function changeCalendarMonth(delta, e) {
  e.stopPropagation();
  dashboard.changeCalendarMonth(delta);
  render();
}

function renderDayCalendar() {
  const [year, month] = dashboard.calendarMonth.split('-').map(Number);
  const monthLabel = new Date(year, month - 1, 1).toLocaleDateString('en', { month: 'long', year: 'numeric' });
  const firstWeekday = new Date(year, month - 1, 1).getDay();
  const daysInMonth = new Date(year, month, 0).getDate();
  const dailyKeys = Object.keys(DAILY);
  const minMonth = dailyKeys.length ? dailyKeys.sort()[0].slice(0, 7) : dashboard.calendarMonth;
  const maxMonth = dailyKeys.length ? dailyKeys.sort().at(-1).slice(0, 7) : dashboard.calendarMonth;

  let html = `<div class="hist-cal-header">
      <button class="hist-cal-nav" onclick="changeCalendarMonth(-1, event)" ${dashboard.calendarMonth <= minMonth ? 'disabled' : ''}>‹</button>
      <div class="hist-cal-month">${monthLabel}</div>
      <button class="hist-cal-nav" onclick="changeCalendarMonth(1, event)" ${dashboard.calendarMonth >= maxMonth ? 'disabled' : ''}>›</button>
    </div>
    <div class="hist-cal-weekdays">${['S','M','T','W','T','F','S'].map(d => `<div>${d}</div>`).join('')}</div>
    <div class="hist-grid hist-cal-grid">`;

  for (let i = 0; i < firstWeekday; i++) html += `<div class="hist-cell"></div>`;
  for (let day = 1; day <= daysInMonth; day++) {
    const date = `${dashboard.calendarMonth}-${String(day).padStart(2, '0')}`;
    const hasData = !!DAILY[date];
    const isActive = date === dashboard.currentDailyDate;
    let cls = 'hist-cell';
    if (isActive) cls += ' active';
    else if (hasData) cls += ' has-data';
    else cls += ' empty';
    const tip = hasData ? `title="${DAILY[date].label}"` : '';
    const click = hasData ? `onclick="showDailySnapshot('${date}')"` : '';
    html += `<div class="${cls}" ${tip} ${click}>${day}</div>`;
  }
  html += '</div>';
  return html;
}

function render() {
  const data = d();
  if (dashboard.currentDailyDate) {
    document.getElementById('period-label').textContent = DAILY[dashboard.currentDailyDate].label;
  } else {
    document.getElementById('period-label').textContent = `Cumulative · as of ${data.label}`;
  }

  document.getElementById('totals').innerHTML = [
    { v: Math.round(data.total_km).toLocaleString('en'), l: 'total km' },
    { v: Math.round(data.total_elev).toLocaleString('en'), l: 'elevation (m)' },
    { v: data.run_count, l: 'activities' },
    { v: (data.leaderboard || []).filter(r => r.acts > 0).length, l: 'active runners' },
    { v: data.athlete_count, l: 'runners' },
  ].map(t => `<div class="total-card fade">
    <div class="val">${t.v}</div><div class="lbl">${t.l}</div>
  </div>`).join('');

  renderRegistrationTree(dashboard.currentBucket(), dashboard.currentGroup);

  document.getElementById('awards').innerHTML = AWARDS.map(def => {
    const a = data[def.key]; if (!a) return '';
    return `<div class="award-card fade">
      <div class="award-emoji">${def.emoji}</div>
      <div>
        <div class="award-title">${def.title}</div>
        <div class="award-name">${esc(a.name)}</div>
        <div class="award-val ${def.muted?'muted':''}">${esc(a.value)}</div>
      </div>
    </div>`;
  }).join('');

  const fun = data.fun_stats || {};
  const funHtml = FUN.map(def => {
    const a = fun[def.key]; if (!a || !a.name) return '';
    return `<div class="fun-card fade">
      <div class="fun-emoji">${def.emoji}</div>
      <div>
        <div class="fun-title">${def.title}</div>
        <div class="fun-name">${esc(a.name)}</div>
        <div class="fun-val">${esc(a.value)}</div>
        ${def.desc ? `<div class="fun-desc">${def.desc}</div>` : ''}
      </div>
    </div>`;
  }).join('');
  document.getElementById('fun-stats').innerHTML = funHtml;
  document.getElementById('fun-section').style.display = funHtml.trim() ? '' : 'none';

  const devs = data.device_stats || [];
  const DEVS_SHOW = 3;
  function devChip(d, i, hidden) {
    const icon = MEDALS[i] || `<span style="color:#ccc;font-size:.75rem;min-width:18px;text-align:center">${i+1}</span>`;
    return `<div class="dev-chip${hidden?' dev-hidden':''}">${icon} <strong>${esc(d.device)}</strong><span style="color:#FC4C02;font-weight:700;margin-left:4px">${d.count}×</span></div>`;
  }
  const extraDevs = devs.slice(DEVS_SHOW);
  const moreHtml = extraDevs.length
    ? extraDevs.map((d,i) => devChip(d, DEVS_SHOW+i, true)).join('') +
      `<button class="dev-more-btn" onclick="var o=this.dataset.open==='1';document.querySelectorAll('#devices .dev-hidden').forEach(function(e){e.classList.toggle('dev-visible')});this.dataset.open=o?'':'1';this.textContent=o?'+ ${extraDevs.length} more':'↑ Show less'">+ ${extraDevs.length} more</button>`
    : '';
  document.getElementById('devices').innerHTML = devs.slice(0, DEVS_SHOW).map((d,i) => devChip(d,i,false)).join('') + moreHtml;
  document.getElementById('device-section').style.display = devs.length ? '' : 'none';

  // Empty state
  const isEmpty = !data.run_count;
  const esEl = document.getElementById('empty-state');
  if (isEmpty) {
    const daySeed = new Date().getDate();
    let msg, bottomHtml;
    const hasPrev = Object.keys(DAILY).length > 0;
    const archiveHtml = hasPrev
      ? `Check out <a onclick="showPrevWeek()">last week's results</a> or <a onclick="toggleHistoryPicker(event)">older archives</a>.`
      : '';
    msg = EMPTY_MSGS[daySeed % EMPTY_MSGS.length];
    bottomHtml = `<div class="es-divider"></div>${archiveHtml ? `<div class="es-action">${archiveHtml}</div>` : ''}<div class="es-hint">This page updates every hour.</div>`;
    esEl.innerHTML = `<h2><span class="es-emoji">${msg.emoji}</span>${msg.title}</h2><p>${msg.body}</p>${bottomHtml}`;
  }
  esEl.style.display = isEmpty ? '' : 'none';
  document.getElementById('totals-section').style.display         = isEmpty ? 'none' : '';
  document.getElementById('registration-section').style.display   = isEmpty ? 'none' : '';
  document.getElementById('awards-section').style.display         = isEmpty ? 'none' : '';
  document.getElementById('leaderboard-section').style.display    = isEmpty ? 'none' : '';
  document.getElementById('group-rankings-section').style.display = isEmpty ? 'none' : '';
  if (isEmpty) {
    document.getElementById('fun-section').style.display    = 'none';
    document.getElementById('device-section').style.display = 'none';
  }
  // History picker — populate day calendar
  const dailyKeys = Object.keys(DAILY);
  document.getElementById('history-wrap').style.display = dailyKeys.length ? '' : 'none';
  if (dailyKeys.length) {
    document.getElementById('btn-7days').style.display = '';
  }
  dashboard.ensureCalendarMonth(dailyKeys);
  document.getElementById('history-picker-list').innerHTML = renderDayCalendar();

  renderLeaderboard(data);
  renderGroupRankings(data);
}

function toggleFilterMenu(key, th) {
  const wasOpen = dashboard.openFilterKey === key;
  closeFilterMenu();
  if (wasOpen) return;
  dashboard.openFilterKey = key;
  const all = d().leaderboard || [];
  const values = [...new Set(all.map(r => r[key]).filter(Boolean))].sort();
  const current = key === 'unit' ? dashboard.filterUnit : dashboard.filterCompany;
  const menu = document.createElement('div');
  menu.className = 'filter-menu';
  menu.id = 'active-filter-menu';
  const allItem = document.createElement('div');
  allItem.className = 'filter-menu-item' + (current === '' ? ' active' : '');
  allItem.textContent = 'All ' + (key === 'unit' ? 'Units' : 'Companies');
  allItem.onclick = e => { e.stopPropagation(); setFilter(key, ''); };
  menu.appendChild(allItem);
  values.forEach(v => {
    const item = document.createElement('div');
    item.className = 'filter-menu-item' + (v === current ? ' active' : '');
    item.textContent = v;
    item.onclick = e => { e.stopPropagation(); setFilter(key, v); };
    menu.appendChild(item);
  });
  document.body.appendChild(menu);
  const rect = th.getBoundingClientRect();
  const mw   = menu.offsetWidth;
  const left  = Math.min(rect.left, window.innerWidth - mw - 8);
  menu.style.top  = (rect.bottom + 4) + 'px';
  menu.style.left = Math.max(8, left) + 'px';
}

function setFilter(key, value) {
  dashboard.setFilter(key, value);
  closeFilterMenu();
  renderLeaderboard(d());
}

function closeFilterMenu() {
  const m = document.getElementById('active-filter-menu');
  if (m) m.remove();
  dashboard.openFilterKey = null;
  ['unit', 'company'].forEach(k => {
    const th = document.getElementById('th-' + k);
    if (th) th.classList.toggle('filter-active', k === 'unit' ? !!dashboard.filterUnit : !!dashboard.filterCompany);
  });
}

document.addEventListener('click', e => {
  if (dashboard.openFilterKey &&
      !e.target.closest('#active-filter-menu') &&
      !e.target.closest('#th-unit') &&
      !e.target.closest('#th-company')) {
    closeFilterMenu();
  }
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeFilterMenu(); });

function renderLeaderboard(data) {
  const all = data.leaderboard || [];
  const activeCount = all.filter(r => r.acts > 0).length;
  document.getElementById('leaderboard-count').textContent = `(${activeCount} active / ${data.athlete_count} total)`;
  // self-heal: reset filter if its value no longer exists in current dataset
  const units     = new Set(all.map(r => r.unit).filter(Boolean));
  const companies = new Set(all.map(r => r.company).filter(Boolean));
  dashboard.resetInvalidFilters(units, companies);
  // sync header active state
  ['unit', 'company'].forEach(k => {
    const th = document.getElementById('th-' + k);
    if (th) th.classList.toggle('filter-active', k === 'unit' ? !!dashboard.filterUnit : !!dashboard.filterCompany);
  });
  // apply filters then sort
  let rows = all;
  if (dashboard.filterUnit)    rows = rows.filter(r => r.unit    === dashboard.filterUnit);
  if (dashboard.filterCompany) rows = rows.filter(r => r.company === dashboard.filterCompany);
  rows = sortedLeaderboard(rows);
  const isByKm = dashboard.currentSort.key === 'km';
  document.getElementById('leaderboard').innerHTML = rows.map((r, i) => `
    <tr>
      <td>${isByKm && MEDALS[i] ? MEDALS[i] : '<span style="color:#ccc;font-size:.75rem">'+(i+1)+'</span>'}</td>
      <td>${esc(r.name)}</td>
      <td>${esc(r.unit||'')}</td>
      <td>${esc(r.company||'')}</td>
      <td class="km-cell">${r.km}</td>
      <td class="gap-cell ${r.gap==='leader'?'leader':''}">${r.gap}</td>
      <td>${r.elev} m</td>
      <td>${r.elev_per_km != null ? r.elev_per_km+' m+/km' : '–'}</td>
      <td>${r.time}</td>
      <td>${r.acts}</td>
      <td>${r.avg_speed}</td>
      <td>${r.longest != null ? r.longest + ' km' : '–'}</td>
    </tr>`).join('') || '<tr><td colspan="12" style="text-align:center;color:#ccc;padding:20px">No activities</td></tr>';
}

function groupBy(data, key) {
  const map = {};
  for (const r of (data.leaderboard || [])) {
    const k = r[key]; if (!k) continue;
    if (!map[k]) map[k] = { name: k, km: 0, active: 0, total: 0 };
    map[k].km += r.km;
    map[k].total++;
    if (r.acts > 0) map[k].active++;
  }
  return Object.values(map).sort((a, b) => b.km - a.km);
}

function unitCompanyRows(leaderboard) {
  const units = {};
  for (const r of (leaderboard || [])) {
    if (!r.unit) continue;
    if (!units[r.unit]) units[r.unit] = { name: r.unit, total: 0, companies: {} };
    units[r.unit].total++;
    const c = r.company || 'Unassigned';
    units[r.unit].companies[c] = (units[r.unit].companies[c] || 0) + 1;
  }
  const rows = [];
  Object.values(units).sort((a, b) => b.total - a.total).forEach(u => {
    rows.push({ level: 'unit', name: u.name, count: u.total });
    Object.entries(u.companies).sort((a, b) => b[1] - a[1]).forEach(([name, count]) => {
      rows.push({ level: 'company', name, count });
    });
  });
  return rows;
}

function renderRegistrationTree(bucket, currentGroup) {
  const rowHtml = r => {
    const pad = { total: 16, branch: 32, unit: 52, company: 72 }[r.level];
    const weight = (r.level === 'total' || r.level === 'branch') ? 700 : 400;
    const color = r.level === 'company' ? '#999' : (r.level === 'unit' ? '#555' : '#FC4C02');
    return `<tr>
      <td style="text-align:left;padding-left:${pad}px;font-weight:${weight}">${esc(r.name)}</td>
      <td style="font-weight:${weight};color:${color}">${r.count}</td>
    </tr>`;
  };
  let rows = [];
  if (currentGroup === 'all') {
    const all = bucket.all;
    rows.push({ level: 'total', name: 'All Registered', count: all.athlete_count });
    [['NSF', 'nsf'], ['NSMen', 'nsmen']].forEach(([label, key]) => {
      const b = bucket[key];
      const count = (b && b.athlete_count) || 0;
      rows.push({ level: 'branch', name: label, count });
      if (b && b.leaderboard) rows = rows.concat(unitCompanyRows(b.leaderboard));
    });
    const unmatched = (all.leaderboard || []).filter(r => !r.unit).length;
    rows.push({ level: 'branch', name: 'Unmatched', count: unmatched });
  } else {
    const b = bucket[currentGroup];
    const count = (b && b.athlete_count) || 0;
    rows.push({ level: 'total', name: currentGroup === 'nsf' ? 'NSF Registered' : 'NSMen Registered', count });
    if (b && b.leaderboard) rows = rows.concat(unitCompanyRows(b.leaderboard));
  }
  document.getElementById('registration-tree').innerHTML =
    '<table><thead><tr><th style="text-align:left">Group</th><th style="text-align:right">Runners</th></tr></thead><tbody>' +
    rows.map(rowHtml).join('') + '</tbody></table>';
}

function renderGroupRankings(data) {
  function tableHtml(groups, label) {
    if (!groups.length) return `<p style="color:#ccc;padding:16px;text-align:center;font-size:.82rem">No ${label} data</p>`;
    return `<table><thead><tr><th>#</th><th style="text-align:left">${label}</th><th>km</th><th>Runners</th></tr></thead><tbody>` +
      groups.map((g, i) => `<tr>
        <td>${MEDALS[i]||'<span style="color:#ccc;font-size:.75rem">'+(i+1)+'</span>'}</td>
        <td style="text-align:left;font-weight:700">${esc(g.name)}</td>
        <td class="km-cell">${Math.round(g.km * 10) / 10}</td>
        <td style="text-align:center">${g.active}</td>
      </tr>`).join('') + '</tbody></table>';
  }
  document.getElementById('unit-rankings').innerHTML    = tableHtml(groupBy(data, 'unit'),    'Unit');
  document.getElementById('company-rankings').innerHTML = tableHtml(groupBy(data, 'company'), 'Company');
}

function showPrevWeek() {
  // Latest Saturday on/before today (Saturday itself if today is one).
  const now = new Date();
  const daysSinceSat = (now.getDay() + 1) % 7;
  const sat = new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysSinceSat);
  const satStr = `${sat.getFullYear()}-${String(sat.getMonth() + 1).padStart(2, '0')}-${String(sat.getDate()).padStart(2, '0')}`;
  const key = Object.keys(DAILY).sort().filter(k => k <= satStr).at(-1);
  if (!key) return;
  showDailySnapshot(key);
  document.getElementById('btn-7days').classList.add('active');
  updateBreadcrumb('7days');
}

const MAIN_SECTION_IDS = ['empty-state', 'totals-section', 'awards-section', 'fun-section',
  'device-section', 'registration-section', 'group-rankings-section', 'leaderboard-section'];

function setMainSectionsVisible(visible) {
  MAIN_SECTION_IDS.forEach(id => { document.getElementById(id).style.display = visible ? '' : 'none'; });
}

function showLeaderboard() {
  dashboard.showLeaderboard();
  document.getElementById('btn-7days').classList.remove('active');
  document.getElementById('btn-history').classList.remove('active');
  document.getElementById('btn-trend').classList.remove('active');
  document.getElementById('btn-leaderboard').classList.add('active');
  document.getElementById('trend-section').style.display = 'none';
  document.getElementById('group-tabs').style.display = '';
  setMainSectionsVisible(true);
  render();
  updateBreadcrumb('leaderboard');
}

function showDailySnapshot(date) {
  dashboard.showDailySnapshot(date);
  closeHistoryPicker();
  document.getElementById('btn-7days').classList.remove('active');
  document.getElementById('btn-history').classList.remove('active');
  document.getElementById('btn-leaderboard').classList.remove('active');
  document.getElementById('btn-trend').classList.remove('active');
  document.getElementById('trend-section').style.display = 'none';
  document.getElementById('group-tabs').style.display = '';
  setMainSectionsVisible(true);
  render();
  updateBreadcrumb('history');
}

function showTrend() {
  dashboard.setSub('trend');
  dashboard.setGroup('all');
  document.querySelectorAll('.group-tab').forEach(t => t.classList.remove('active'));
  document.querySelector('.group-tab[onclick="setGroup(\'all\', this)"]').classList.add('active');
  document.getElementById('group-tabs').style.display = 'none';
  document.getElementById('btn-7days').classList.remove('active');
  document.getElementById('btn-history').classList.remove('active');
  document.getElementById('btn-leaderboard').classList.remove('active');
  document.getElementById('btn-trend').classList.add('active');
  setMainSectionsVisible(false);
  document.getElementById('trend-section').style.display = '';
  renderTrend();
  updateBreadcrumb('trend');
}

// ---------------------------------------------------------------------------
// Trend — data series helpers (read straight from DAILY, no backend changes)
// ---------------------------------------------------------------------------

function sortedDates() {
  return Object.keys(DAILY).sort();
}

function activeCount(bucket) {
  return ((bucket && bucket.leaderboard) || []).filter(r => r.acts > 0).length;
}

function trendSeries(group, metric) {
  return sortedDates().map(date => {
    const bucket = DAILY[date][group];
    return { x: DAILY[date].label, y: (bucket && bucket[metric]) || 0 };
  });
}

function trendParticipationSeries(group) {
  return sortedDates().map(date => {
    const bucket = DAILY[date][group];
    const total = (bucket && bucket.athlete_count) || 0;
    const active = activeCount(bucket);
    return { x: DAILY[date].label, y: total ? Math.round((active / total) * 1000) / 10 : 0 };
  });
}

function trendAvgKmSeries(group) {
  return sortedDates().map(date => {
    const bucket = DAILY[date][group];
    const active = activeCount(bucket);
    const km = (bucket && bucket.total_km) || 0;
    return { x: DAILY[date].label, y: active ? Math.round((km / active) * 10) / 10 : 0 };
  });
}

function sundaySnapshots(group) {
  const dates = sortedDates();
  if (!dates.length) return [];
  const first = new Date(dates[0] + 'T00:00:00');
  const last  = new Date(dates[dates.length - 1] + 'T00:00:00');
  first.setDate(first.getDate() + ((7 - first.getDay()) % 7)); // roll to first Sunday
  const rows = [];
  for (let d = new Date(first); d <= last; d.setDate(d.getDate() + 7)) {
    const iso = d.toISOString().slice(0, 10);
    const snapDate = [...dates].reverse().find(dt => dt <= iso);
    if (!snapDate) continue;
    const bucket = DAILY[snapDate][group] || {};
    const active = activeCount(bucket);
    const registered = bucket.athlete_count || 0;
    rows.push({
      weekOf: d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }),
      km: bucket.total_km || 0,
      acts: bucket.run_count || 0,
      active, registered,
      participation: registered ? Math.round((active / registered) * 1000) / 10 : 0,
      avgKm: active ? Math.round(((bucket.total_km || 0) / active) * 10) / 10 : 0,
      elev: bucket.total_elev || 0,
    });
  }
  return rows;
}

function subgroupsFromLatest(field, cap) {
  const dates = sortedDates();
  if (!dates.length) return [];
  const latest = DAILY[dates[dates.length - 1]].all.leaderboard || [];
  const counts = {};
  latest.forEach(r => { if (r[field]) counts[r[field]] = (counts[r[field]] || 0) + (r.acts > 0 ? 1 : 0); });
  return Object.keys(counts).sort((a, b) => counts[b] - counts[a]).slice(0, cap);
}

function countsBySubgroup(leaderboard, field, keys) {
  const active = {}, registered = {};
  keys.forEach(k => { active[k] = 0; registered[k] = 0; });
  (leaderboard || []).forEach(r => {
    const k = r[field];
    if (!k || !(k in active)) return;
    registered[k]++;
    if (r.acts > 0) active[k]++;
  });
  return { active, registered };
}

function runnerSeriesBySubgroup(view, kind) {
  const dates = sortedDates();
  const labels = dates.map(dt => DAILY[dt].label);
  const countFor = kind === 'registered'
    ? bucket => (bucket && bucket.athlete_count) || 0
    : bucket => activeCount(bucket);

  if (view === 'all') {
    return [
      { label: kind === 'registered' ? 'Registered' : 'Active', dashed: false, colorIdx: 0,
        points: dates.map((date, i) => ({ x: labels[i], y: countFor(DAILY[date].all) })) },
    ];
  }
  if (view === 'nsf-nsmen') {
    const groups = [{ key: 'nsf', label: 'NSF', colorIdx: 0 }, { key: 'nsmen', label: 'NSMen', colorIdx: 1 }];
    return groups.map(g => ({
      label: g.label, dashed: false, colorIdx: g.colorIdx,
      points: dates.map((date, i) => ({ x: labels[i], y: countFor(DAILY[date][g.key]) })),
    }));
  }

  // view === 'unit' | 'company'
  const field = view;
  const keys = subgroupsFromLatest(field, 8);
  const perDate = dates.map(date => countsBySubgroup(DAILY[date].all.leaderboard, field, keys));
  const bucketKey = kind === 'registered' ? 'registered' : 'active';
  return keys.map((k, ki) => ({
    label: k, dashed: false, colorIdx: ki,
    points: dates.map((date, i) => ({ x: labels[i], y: perDate[i][bucketKey][k] })),
  }));
}

// ---------------------------------------------------------------------------
// Trend — LineChart rendering (own class, separate from Dashboard's state/data)
// ---------------------------------------------------------------------------

class LineChart {
  constructor(containerId, opts) {
    this.container = document.getElementById(containerId);
    this.opts = Object.assign({
      width: 600, height: 220,
      padding: { top: 14, right: 40, bottom: 26, left: 46 },
      yFormat: v => Math.round(v).toLocaleString('en'),
    }, opts || {});
    this.tooltip = document.getElementById('line-chart-tooltip');
  }

  render(series) {
    const s = (series || []).filter(ser => ser.points && ser.points.length);
    if (!s.length) {
      this.container.innerHTML = '<div class="line-chart-empty">No data yet</div>';
      return;
    }
    this.series = s;
    const { width, height, padding, yFormat } = this.opts;
    const plotW = width - padding.left - padding.right;
    const plotH = height - padding.top - padding.bottom;
    const dates = s[0].points.map(p => p.x);
    const n = dates.length;
    const allY = s.flatMap(ser => ser.points.map(p => p.y));
    const maxY = Math.max(1, ...allY);
    const xAt = i => padding.left + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const yAt = v => padding.top + plotH - (v / maxY) * plotH;
    this.xAt = xAt;
    this.dates = dates;

    const GRID_STEPS = 4;
    let axesSvg = '';
    for (let i = 0; i <= GRID_STEPS; i++) {
      const v = Math.round((maxY / GRID_STEPS) * i);
      const y = yAt(v);
      axesSvg += `<line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="#e1e0d9" stroke-width="1"/>`;
      axesSvg += `<text x="${padding.left - 8}" y="${y + 3}" text-anchor="end" font-size="9" fill="#898781">${esc(yFormat(v))}</text>`;
    }
    const tickIdx = n === 1 ? [0] : [...new Set([0, Math.floor((n - 1) / 2), n - 1])];
    tickIdx.forEach(i => {
      axesSvg += `<text x="${xAt(i)}" y="${height - 6}" text-anchor="middle" font-size="9" fill="#898781">${esc(dates[i])}</text>`;
    });

    let marksSvg = '';
    s.forEach(ser => {
      const color = LINE_COLORS[ser.colorIdx % LINE_COLORS.length];
      const pts = ser.points.map((p, i) => `${xAt(i)},${yAt(p.y)}`).join(' ');
      const dash = ser.dashed ? ' stroke-dasharray="5,4"' : '';
      marksSvg += `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2"${dash} stroke-linejoin="round" stroke-linecap="round"/>`;
      const last = ser.points[ser.points.length - 1];
      marksSvg += `<circle cx="${xAt(n - 1)}" cy="${yAt(last.y)}" r="4" fill="${color}" stroke="#fff" stroke-width="2"/>`;
    });
    if (s.length === 1) {
      const last = s[0].points[n - 1];
      marksSvg += `<text x="${xAt(n - 1) + 8}" y="${yAt(last.y) + 3}" font-size="10" font-weight="700" fill="#52514e">${esc(yFormat(last.y))}</text>`;
    }

    let hitSvg = `<line class="lc-crosshair" x1="0" y1="${padding.top}" x2="0" y2="${padding.top + plotH}" stroke="#c3c2b7" stroke-width="1" style="display:none"/>`;
    for (let i = 0; i < n; i++) {
      const x0 = i === 0 ? padding.left : (xAt(i - 1) + xAt(i)) / 2;
      const x1 = i === n - 1 ? width - padding.right : (xAt(i) + xAt(i + 1)) / 2;
      hitSvg += `<rect x="${x0}" y="${padding.top}" width="${Math.max(1, x1 - x0)}" height="${plotH}" fill="transparent" data-idx="${i}"/>`;
    }

    this.container.innerHTML =
      `<svg class="line-chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${axesSvg}${marksSvg}${hitSvg}</svg>` +
      (s.length > 1 ? this._legendHtml(s) : '');
    this._bindHover();
  }

  _legendHtml(s) {
    return `<div class="line-chart-legend">${s.map(ser => {
      const color = LINE_COLORS[ser.colorIdx % LINE_COLORS.length];
      const style = ser.dashed ? `border-color:${color}` : `background:${color}`;
      return `<span class="lg-item"><span class="lg-swatch${ser.dashed ? ' dashed' : ''}" style="${style}"></span>${esc(ser.label)}</span>`;
    }).join('')}</div>`;
  }

  _bindHover() {
    const svg = this.container.querySelector('svg');
    const crosshair = svg.querySelector('.lc-crosshair');
    svg.querySelectorAll('rect[data-idx]').forEach(rect => {
      rect.addEventListener('pointermove', e => this._showTooltip(e, +rect.dataset.idx, crosshair));
      rect.addEventListener('pointerleave', () => this._hideTooltip(crosshair));
    });
  }

  _showTooltip(e, idx, crosshair) {
    const x = this.xAt(idx);
    crosshair.setAttribute('x1', x);
    crosshair.setAttribute('x2', x);
    crosshair.style.display = '';
    const rows = this.series.map(ser => {
      const color = LINE_COLORS[ser.colorIdx % LINE_COLORS.length];
      const v = ser.points[idx] ? ser.points[idx].y : 0;
      return `<div class="lct-row"><span class="lct-swatch" style="background:${color}"></span>${esc(ser.label)}<span class="lct-val">${esc(this.opts.yFormat(v))}</span></div>`;
    }).join('');
    this.tooltip.innerHTML = `<div class="lct-date">${esc(this.dates[idx])}</div>${rows}`;
    this.tooltip.style.display = 'block';
    this.tooltip.style.left = (e.clientX + 14) + 'px';
    this.tooltip.style.top = (e.clientY - 10) + 'px';
  }

  _hideTooltip(crosshair) {
    crosshair.style.display = 'none';
    this.tooltip.style.display = 'none';
  }
}

let trendCharts = null;
function ensureTrendCharts() {
  if (trendCharts) return trendCharts;
  trendCharts = {
    distance:      new LineChart('trend-distance-chart',      { yFormat: v => Math.round(v).toLocaleString('en') }),
    activities:    new LineChart('trend-activities-chart',    { yFormat: v => Math.round(v).toLocaleString('en') }),
    activeRunners:    new LineChart('trend-active-runners-chart',    { yFormat: v => Math.round(v).toLocaleString('en') }),
    registeredRunners: new LineChart('trend-registered-runners-chart', { yFormat: v => Math.round(v).toLocaleString('en') }),
    participation: new LineChart('trend-participation-chart', { yFormat: v => v + '%' }),
    avgkm:         new LineChart('trend-avgkm-chart',         { yFormat: v => v + ' km' }),
    elevation:     new LineChart('trend-elevation-chart',     { yFormat: v => Math.round(v).toLocaleString('en') + ' m' }),
  };
  return trendCharts;
}

function renderTrendWeeklyTable() {
  const rows = sundaySnapshots(dashboard.currentGroup);
  const body = document.getElementById('trend-weekly-body');
  body.textContent = '';
  if (!rows.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 8; td.style.textAlign = 'center'; td.style.color = '#898781';
    td.textContent = 'No Sunday snapshots yet';
    tr.appendChild(td); body.appendChild(tr);
    return;
  }
  rows.forEach(r => {
    const tr = document.createElement('tr');
    [r.weekOf, r.km.toLocaleString('en'), r.acts.toLocaleString('en'), r.active, r.registered,
     r.participation + '%', r.avgKm + ' km', r.elev.toLocaleString('en')]
      .forEach((val, i) => {
        const td = document.createElement('td');
        if (i > 0) td.style.textAlign = 'right';
        td.textContent = val;
        tr.appendChild(td);
      });
    body.appendChild(tr);
  });
}

function renderTrend() {
  const charts = ensureTrendCharts();
  const g = dashboard.currentGroup;
  const label = GROUP_LABELS[g];
  renderTrendWeeklyTable();
  charts.distance.render([{ label, dashed: false, colorIdx: 0, points: trendSeries(g, 'total_km') }]);
  charts.activities.render([{ label, dashed: false, colorIdx: 0, points: trendSeries(g, 'run_count') }]);
  charts.participation.render([{ label, dashed: false, colorIdx: 0, points: trendParticipationSeries(g) }]);
  charts.avgkm.render([{ label, dashed: false, colorIdx: 0, points: trendAvgKmSeries(g) }]);
  charts.elevation.render([{ label, dashed: false, colorIdx: 0, points: trendSeries(g, 'total_elev') }]);
  renderTrendRunners();
}

function renderTrendRunners() {
  const charts = ensureTrendCharts();
  const view = dashboard.trendRunnerView;
  charts.activeRunners.render(runnerSeriesBySubgroup(view, 'active'));
  charts.registeredRunners.render(runnerSeriesBySubgroup(view, 'registered'));
}

function setTrendRunnerView(view, el) {
  dashboard.trendRunnerView = view;
  document.querySelectorAll('#trend-runner-toggle .tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  renderTrendRunners();
}


showLeaderboard();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

LEDGER_PATH = Path(__file__).parent / "activity-ledger.json"
CLEAN_LEDGER_PATH = Path(__file__).parent / "activity-ledger-clean.json"


class Ledger:
    """The raw (append-only) activity ledger plus its deduped "clean" derivative.

    ponytail: no activity id/timestamp in Strava's club feed, so new entries
    are found by anchoring on the ledger's recent entries inside the fresh
    fetch. Strava can revise a logged activity's own fields after the fact
    (elevation correction), so a single mutated entry shouldn't sink the
    whole anchor: match each of the last ANCHOR_WINDOW ledger entries
    independently and require only ANCHOR_MIN_MATCHES of them to be found.
    Matches must also land in the same relative order as the ledger entries
    they came from (both are newest-first) — two unrelated activities from
    the same runner's routine loop can coincidentally share a content key,
    and without an order check that coincidence alone could satisfy
    ANCHOR_MIN_MATCHES and pick a wrong cut point. Anchor still misses if
    too few match or matches are out of order (>197 new activities in an
    hour, or multiple anchored activities edited/deleted) -> append
    everything, accept occasional double-count.
    """

    ANCHOR_WINDOW = 8
    ANCHOR_MIN_MATCHES = 2

    def __init__(self, path: Path, clean_path: Path):
        self.path = path
        self.clean_path = clean_path
        self.activities = self._load(path)
        self.anchor_missed = False

    @staticmethod
    def _load(path: Path) -> list:
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _save(path: Path, ledger: list) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _activity_key(act: dict) -> tuple:
        """Content key used for both anchor matching and dedup.

        total_elevation_gain is excluded — Strava revises it after the fact
        (elevation correction), which would otherwise change an already-
        ledgered activity's key and let it slip past the existing_keys dedup
        as a "new" entry, double-counting it.
        """
        athlete = act.get("athlete") or {}
        name = (athlete.get("firstname", ""), athlete.get("lastname", ""))
        return (
            name,
            act.get("name"),
            act.get("distance"),
            act.get("moving_time"),
            act.get("elapsed_time"),
            act.get("device_name"),
        )

    def merge(self, fresh: list, now_iso: str) -> int:
        """Merge freshly fetched activities (newest-first) into self.activities
        (newest-first), in place. Returns the count of new entries.

        The anchor match is only a fast path for finding new activities — Strava's
        club feed has no activity id/timestamp, so the anchor can legitimately miss
        (>197 new activities in an hour, or too many anchored activities
        edited/deleted). Content-key dedup below is the actual correctness
        guarantee: it runs unconditionally, so a missed or stale anchor can
        degrade to "append everything" but can never reintroduce an activity
        already in the ledger.
        """
        ledger = self.activities
        if not ledger:
            new_entries, self.anchor_missed = fresh, False
            print("  Ledger empty — no anchor to match, treating full fetch as new.")
        else:
            candidates = ledger[:self.ANCHOR_WINDOW]
            fresh_index = {}
            for i, a in enumerate(fresh):
                fresh_index.setdefault(self._activity_key(a), i)

            # candidates and fresh are both newest-first, so genuine matches
            # must appear in fresh in the same relative order as in candidates.
            matched_at = [fresh_index[self._activity_key(a)] for a in candidates
                          if self._activity_key(a) in fresh_index]
            ordered = all(a < b for a, b in zip(matched_at, matched_at[1:]))
            required = min(self.ANCHOR_MIN_MATCHES, len(candidates))
            self.anchor_missed = len(matched_at) < required or not ordered

            if self.anchor_missed:
                new_entries = fresh
                if ordered:
                    print(f"  Anchor NOT found: only {len(matched_at)}/{required} of last "
                          f"{len(candidates)} ledger entries matched in fresh fetch.")
                else:
                    print(f"  Anchor REJECTED: {len(matched_at)} matches found but out of "
                          f"order ({matched_at}) — likely a coincidental content-key collision.")
            else:
                cut_at = min(matched_at)
                new_entries = fresh[:cut_at]
                print(f"  Anchor found: {len(matched_at)}/{len(candidates)} of last "
                      f"{len(candidates)} ledger entries matched, cutting at fresh[{cut_at}].")

        existing_keys = {self._activity_key(a) for a in ledger}
        new_entries = [a for a in new_entries if self._activity_key(a) not in existing_keys]

        stamped = [{**a, "ingested_at": now_iso} for a in new_entries]
        self.activities = stamped + ledger
        return len(new_entries)

    def save(self):
        self._save(self.path, self.activities)

    @staticmethod
    def _same_upload_batch(a: dict, b: dict) -> bool:
        aa, bb = a.get("athlete") or {}, b.get("athlete") or {}
        return (a.get("ingested_at") == b.get("ingested_at")
                and aa.get("firstname") == bb.get("firstname")
                and aa.get("lastname") == bb.get("lastname"))

    @classmethod
    def dedup_consecutive(cls, entries: list) -> list:
        """Collapse runs of *consecutive* same-athlete activities ingested in the
        same batch (e.g. multiple runs uploaded together) into the single
        longest-distance entry, preserving order."""
        result, group = [], []
        for act in entries:
            if group and not cls._same_upload_batch(group[-1], act):
                result.append(max(group, key=lambda a: a.get("distance") or 0))
                group = []
            group.append(act)
        if group:
            result.append(max(group, key=lambda a: a.get("distance") or 0))
        return result

    def build_clean(self, new_count: int) -> list:
        """Update and persist the clean (deduped) ledger given how many new
        raw entries were just merged in by merge()."""
        if self.clean_path.exists():
            clean_ledger = self.dedup_consecutive(self.activities[:new_count]) + self._load(self.clean_path)
        else:
            # bootstrap: no prior clean ledger, dedup the whole history at once
            clean_ledger = self.dedup_consecutive(self.activities)
        self._save(self.clean_path, clean_ledger)
        return clean_ledger


MEMBERS_LEDGER_PATH = Path(__file__).parent / "members-ledger.json"


class MembersLedger:
    """Append-only ledger of club members, structured like the activity
    Ledger. Each entry is a raw member dict (as fetched from Strava) plus
    'ingested_at' — stamped only the first time that member is seen, so it
    doubles as a proxy "registration date" (Strava's club API exposes none).
    Existing entries are never rewritten, matching how the activity ledger
    treats ingested_at as immutable once stamped.
    """

    def __init__(self, path: Path):
        self.path = path
        # [{...raw member fields, "ingested_at": iso}, ...] — loaded inline,
        # unlike Ledger._load this isn't reused elsewhere in the class.
        self.members = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    @staticmethod
    def _member_key(m: dict):
        """id when Strava provides one, else the raw name pair — mirrors
        Ledger._activity_key's role as a stable identity for dedup."""
        if m.get("id") is not None:
            return m["id"]
        return (m.get("firstname"), m.get("lastname"))

    def merge(self, fresh: list, now_iso: str) -> int:
        """Append only members not already in the ledger, stamped with
        now_iso. Returns the count of newly-registered members."""
        existing_keys = {self._member_key(m) for m in self.members}
        new_entries = [m for m in fresh if self._member_key(m) not in existing_keys]
        stamped = [{**m, "ingested_at": now_iso} for m in new_entries]
        self.members = stamped + self.members
        return len(new_entries)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.members, ensure_ascii=False), encoding="utf-8")

    def members_for_date(self, date_str: str) -> list:
        """Every member whose first-seen date is on or before date_str —
        i.e. who was already registered as of that date."""
        return [m for m in self.members if m.get("ingested_at", "")[:10] <= date_str]


def build_daily_history(clean_ledger: list, members_ledger: MembersLedger, roll: NominalRoll = None) -> dict:
    """Derive {date: {date, label, all, nsf, nsmen}} for every date that ever
    saw a new ledger entry, by re-running build_grouped_data() against the
    ledger cumulative up to and including that date.

    clean_ledger has no per-activity date (Strava's club feed doesn't expose
    one) — ingested_at (scrape time) is the only timeline signal, and is
    exactly what "today" is already computed from, so this reproduces every
    historical date's stats with full fidelity, not just an approximation.

    members_ledger.members_for_date() supplies the members actually
    registered as of each date, instead of reusing today's live roster for
    every historical date.
    """
    dates = sorted({a["ingested_at"][:10] for a in clean_ledger if a.get("ingested_at")})
    result = {}
    for date_str in dates:
        subset = [a for a in clean_ledger if a.get("ingested_at", "")[:10] <= date_str]
        y, m, d = (int(p) for p in date_str.split("-"))
        label = f"{d}.{m}.{y}"
        day_members = members_ledger.members_for_date(date_str)
        result[date_str] = {"date": date_str, "label": label,
                             **build_grouped_data(subset, day_members, label, roll)}
    return result


ANNOUNCEMENT_PATH = Path(__file__).parent.parent / "annoucement.md"


def build_announcement_html() -> str:
    """Read annoucement.md (# Title, then body text) into a dismissible banner.

    Returns '' if the file is missing or has no title, hiding the banner.
    """
    if not ANNOUNCEMENT_PATH.exists():
        return ""
    lines = ANNOUNCEMENT_PATH.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        return ""
    title = lines[0].lstrip("#").strip()
    body = "\n".join(lines[1:]).strip()
    if not title:
        return ""
    import html as html_lib
    title_html = html_lib.escape(title)
    body_html = html_lib.escape(body)
    return (
        '<div class="announcement" id="announcement">'
        f'<div class="announcement-title">{title_html}</div>'
        f'<div class="announcement-body">{body_html}</div>'
        '<button class="announcement-close" onclick="document.getElementById(\'announcement\').style.display=\'none\'" title="Close">✕</button>'
        '</div>'
    )


def run():
    """A full fetch -> merge -> compute -> render -> write pass."""
    roll = NominalRoll()
    ledger = Ledger(LEDGER_PATH, CLEAN_LEDGER_PATH)
    members_ledger = MembersLedger(MEMBERS_LEDGER_PATH)
    strava_client = StravaClient(config)

    print("Fetching data from Strava...")

    strava_client.get_access_token()
    members = strava_client.fetch_club_members()

    print("  Fetching latest club activities...")
    fresh = strava_client.fetch_club_activities()
    now_dt = now_tz(config)
    now_iso = now_dt.strftime("%Y-%m-%dT%H:%M")

    stored_count = len(ledger.activities)
    new_count = ledger.merge(fresh, now_iso)
    if ledger.anchor_missed and stored_count:
        print("  WARNING: ledger anchor not found in fresh activities — "
              "appended full fetch, some activities may be double-counted.")
    print(f"  {new_count} new activities appended to ledger.")
    ledger.save()

    new_member_count = members_ledger.merge(members, now_iso)
    members_ledger.save()
    print(f"  {new_member_count} new members registered.")

    clean_ledger = ledger.build_clean(new_count)
    if config.start_date:
        cutoff = config.start_date
        clean_ledger = [a for a in clean_ledger
                         if a.get("ingested_at", "")[:10] >= cutoff]

    date_label = f"{now_dt.day}.{now_dt.month}.{now_dt.year}"
    # clean_ledger entries are all ingested_at <= now, so this *is* the
    # cumulative-to-date total — no per-week/per-day filtering needed.
    today_data = build_grouped_data(clean_ledger, members, date_label, roll)
    daily_snapshots = build_daily_history(clean_ledger, members_ledger, roll)

    data = {"today": today_data}

    _, human_label = now_label(config)

    weather = fetch_weather(config)
    if weather["ok"]:
        weather_html = (
            f'<span class="weather-widget">'
            f'{weather["icon"]} <strong>{weather["temp"]}°C</strong>'
            f' · {weather["desc"]}'
            f' · 💨 {weather["wind"]} km/h'
            f'</span>'
        )
    else:
        weather_html = ""

    # Club short name — first letters or first word
    club_name = config.club_name
    words = club_name.split()
    club_short = "".join(w[0] for w in words).upper() if len(words) > 1 else club_name[:4].upper()

    announcement_html = build_announcement_html()

    html = TEMPLATE
    html = html.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__DAILY_DATA__", json.dumps(daily_snapshots, ensure_ascii=False))
    html = html.replace("__UPDATED_HUMAN__", human_label)
    html = html.replace("__WEATHER__", weather_html)
    html = html.replace("__ANNOUNCEMENT__", announcement_html)
    html = html.replace("__CLUB_NAME__", club_name)
    html = html.replace("__CLUB_SHORT__", club_short)
    html = html.replace("__CLUB_ID__", config.strava_club_id)

    out_path = Path(__file__).parent.parent / "index.html"
    out_path.write_text(html, encoding="utf-8")

    w = data["today"]["all"]
    print(f"Generated: {out_path}")
    print(f"  Cumulative: {w.get('count', 0)} activities, {w.get('athlete_count', 0)} runners")


if __name__ == "__main__":
    run()
