# 8SAB 50th Anniversary Virtual Challenge — Strava Dashboard

[![Tests](https://github.com/choonyongchan/BdeAnnivVirtChallenge/actions/workflows/test.yml/badge.svg)](https://github.com/choonyongchan/BdeAnnivVirtChallenge/actions/workflows/test.yml)
[![Coverage](https://codecov.io/gh/choonyongchan/BdeAnnivVirtChallenge/branch/main/graph/badge.svg)](https://codecov.io/gh/choonyongchan/BdeAnnivVirtChallenge)
[![Users covered](https://img.shields.io/endpoint?url=https://choonyongchan.github.io/BdeAnnivVirtChallenge/user-count.json)](https://choonyongchan.github.io/BdeAnnivVirtChallenge/)

**→ [View the live dashboard](https://choonyongchan.github.io/BdeAnnivVirtChallenge/)**

A live leaderboard for the 8SAB 50th Anniversary Virtual Challenge. A scheduled job
reads the challenge's Strava club, matches each athlete against the unit nominal
roll, and writes the whole dashboard into one static `index.html` on GitHub Pages.
It refreshes every hour. There is no server and no database: every sort, filter,
chart, and history view runs in your browser off data baked into the page.

![The dashboard: totals, awards and fun stats](src/docs/dashboard-overview.png)

---

# User Guide

Open [the dashboard](https://choonyongchan.github.io/BdeAnnivVirtChallenge/) in any
browser. Nothing to install, no login.

The top of the page shows club totals (distance, elevation, activities, active
runners, registered runners) and award cards: Distance King, Climbing King,
Marathoner, Fastest, Longest Run, Mountain Goat, Flat Runner, and Break King for
the most time spent stopped mid-activity. A card appears only once someone
qualifies.

Below that is the runner leaderboard. It lists every registered runner, including
those who haven't run yet. Click a column to sort; use the Unit and Company menus
to filter. Alongside it sit unit rankings, company rankings, and a registration
tree, all built from the roll. Three tabs (All, NSF/Regular, NSMan/Alumni) split
everyone by `Type of service`.

The History picker opens a calendar of past cumulative standings, one per day the
nightly scan ran, with a shortcut to last week (as of its Sunday). The Trend view adds a weekly Sunday snapshot table and
cumulative charts for distance, activities, runners, participation rate, and
elevation, with a breakdown by group, unit, or company.

A recording-device breakdown, local weather, and a dismissible announcement banner
round out the page.

![Runner leaderboard with unit and company columns](src/docs/dashboard-leaderboard.png)

![Trend view: weekly snapshot table and cumulative charts](src/docs/dashboard-trend.png)

---

# Developer Guide

## Architecture

Strava shut off its public club API in 2026, so the pipeline drives a logged-in
browser instead. One `python -m src.main` run does five things in order:

```
python -m src.main [--full]      (--full is implied on the 23:xx run)
  ├─ check_auth()                 src/main.py          is src/auth_state.json a valid session?
  ├─ fetch_leaderboard()  [full]  src/activities/      club leaderboard, this + last week (top 100)
  ├─ scrape_members(leaderboard)  src/members/         headline member count → member_count.json;
  │                                                    club feed: new foot activities → ledger,
  │                                                    new feed (+ leaderboard) athletes → members.csv
  ├─ sync_weeks(leaderboard) [full] src/activities/    every member's profile week (this week; last
  │                                                    week too on Mondays) → daily.csv snapshot +
  │                                                    ledger; leaderboard figures override the profile's
  ├─ generate.run()               src/dashboard/       snapshot + feed activities since it, roll, weather
  │      stats.py   → totals, awards, leaderboard, devices
  │      names.py   → match truncated Strava names to the roll (unit / company / service)
  │      renderer.py→ substitute into template.html
  │                                                    → write index.html
  └─ publish_dashboard()          src/main.py          commit and push index.html
```

Strava is the authority on distance, so the dashboard's totals are **Strava's own
weekly figures per member** (`daily.csv`: one row per member per day of the nightly
scan, holding that Monday–Sunday week's distance, moving time, elevation and foot
activities so far). Each member's profile week lists that week's
activities; their foot activities (Run, Walk, Hike, …) are summed. The club
leaderboard — which Strava keeps for two weeks only, top 100 — overrides those sums,
since it also counts runs this account can't see (followers-only, private profiles).
Each night adds a new dated row and earlier days are never rewritten (except last
Sunday's, refreshed on Monday), so the history keeps one point per day; a day with no
scan carries the previous one forward.

Scanning ~1,050 profiles costs as many requests, and Strava answers too many with a
`429` block, so that **full scan runs once a day** (the 23:47 run) and stops at the
first `429`, keeping what it fetched. Every row it writes is stamped `synced_at`. The
**hourly runs read only the club feed** (a handful of requests): its foot activities
go into the activity ledger (`activities.csv`), and the dashboard shows the nightly
snapshot plus every ledger activity scraped after that athlete's `synced_at`. The
next night's scan overwrites them with Strava's figures. Feed runs from private
profiles, which no scan can see, keep counting this way. The ledger (feed + profile
activities) also feeds the per-activity awards (Longest Run, Fastest, Break King,
Mountain Goat, devices).

Members come from the club feed, the leaderboard and the existing `members.csv`;
some members never show up in either, so the dashboard's member total is Strava's
headline count. All scrapers share one Playwright session (`src/strava_session.py`)
that spoofs a normal browser; there's no OAuth and no API tokens. A failed run is
retried by the next one. `NominalRoll` in
`src/dashboard/names.py` resolves Strava's truncated club-feed names (e.g.
`"Siva R."`) back to the right roster entry. `renderer.render()` substitutes the
computed data into `src/dashboard/template.html` to produce `index.html`, which is
generated output — edit the template and regenerate, since direct edits to
`index.html` are overwritten.

## Key commands

```bash
git clone https://github.com/choonyongchan/BdeAnnivVirtChallenge.git
cd BdeAnnivVirtChallenge
pip install -r requirements.txt
python -m playwright install chromium    # add --with-deps on Linux
```

| Command | Does |
|---|---|
| `python -m src.login` | Opens a visible browser to log in to Strava; writes `src/auth_state.json`, the session every scraper reuses. |
| `python -m src.nominal_roll.nominal_roll "<raw FormSG export.csv>"` | Cleans a registration export and merges it into `src/nominal_roll/nominal_roll.csv` — exports are incremental, so new registrants are appended and a re-registration replaces that person's row. Delete the roll first to rebuild it from scratch. Without this file the dashboard still builds, but nobody gets a unit, company, or full name. |
| `python -m src.main` | Runs the pipeline: scrape the feed, generate, and push. `--full` also takes the nightly snapshot (leaderboard + every member's profile week), as the 23:xx run does. |
| `python -m src.activities.activities --setup` | One-off: syncs every week since `challenge_start` into `daily.csv` (dated by each week's Sunday) (without `--setup`: this week, plus last week on Mondays — what the pipeline does). |
| `python -m pytest test/e2e/test_strava_parity.py --live` | Checks the dashboard against live Strava: every leaderboard athlete's this-week and last-week figures, and the member total. Run it right after `python -m src.main --full`. |
| `python -m src.dashboard.generate` | Rebuilds `index.html` from the CSVs you already have, without scraping or touching git. |
| `python -m pytest test/ -q` | Runs the test suite (`test/unit`, `test/integration`, `test/e2e`). Every fixture is synthetic. |

`index.html` is self-contained — open the file directly, no local server needed.

Settings live in `src/config.yaml` and nowhere else. There is no `.env` and the
code reads no environment variables; CI supplies secrets separately.

| Key | Default | Purpose |
|---|---|---|
| `club.name` | `8SAB 50th Anniversary Virtual Challenge` | Dashboard title |
| `club.id` | `2211123` | Strava club the scrapers read |
| `challenge_start` | `2026-09-01` | Activities before this local date don't count (code default is `2026-09-14`; the yaml value wins) |
| `timezone` | `Asia/Singapore` | Display timezone for the dashboard |
| `weather.latitude` / `weather.longitude` | `1.3835` / `103.7478` | Weather widget location |
| `announcement_path` | `src/announcement.md` | Banner source file |
| `browser.channel` | `""` | Empty = Playwright's bundled Chromium; `chrome`/`msedge` drive a system browser |
| `browser.headless` | `true` | Set `false` to watch a scrape |

To show the banner, put a title on the first line of `src/announcement.md` (a
leading `#` is stripped) and the body below it. An empty or missing file hides it.
Content is HTML-escaped.

## How it deploys

The pipeline runs locally: `scripts/run_pipeline.ps1` (Windows Task Scheduler, see
below) runs `python -m src.main` and commits `daily.csv`, `activities.csv`,
`members.csv`, `member_count.json`, and `index.html` to `main`. `.github/workflows/deploy.yml`
then publishes `index.html` (and the `user-count.json` badge data file) to GitHub
Pages via `actions/deploy-pages` whenever a push changes either file.

Publishing needs **Settings → Pages → Source = GitHub Actions**. `index.html` is
the entire site. The CSV ledgers are committed for history; `auth_state.json` and
`nominal_roll.csv` never are.

A separate `.github/workflows/test.yml` runs the suite with coverage on every push
and pull request to `main` and uploads results to Codecov — this is what the
badges at the top track. Codecov needs a `CODECOV_TOKEN` repo secret from
[codecov.io](https://codecov.io/) to upload.

### Windows Task Scheduler (local hourly runs)

An operator's Windows machine runs the pipeline hourly via Task Scheduler.
`scripts/run_pipeline.ps1` runs the pipeline and commits the ledgers;
`src/auth_state.json` and `src/nominal_roll/nominal_roll.csv` must exist on disk
on that machine. It's scheduled at **minute 47** of every hour.

Registered once with:

```powershell
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument '-NoProfile -ExecutionPolicy Bypass -File "<repo>\scripts\run_pipeline.ps1"'
$next47 = Get-Date -Minute 47 -Second 0
if ($next47 -le (Get-Date)) { $next47 = $next47.AddHours(1) }
$Trigger = New-ScheduledTaskTrigger -Once -At $next47 -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet -DontStopOnIdleEnd -MultipleInstances IgnoreNew -StartWhenAvailable
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName "BdeAnnivVirtChallenge-HourlyPipeline" -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Runs the Strava dashboard pipeline hourly at :47."
```

`-LogonType S4U` runs the task whether or not anyone is logged into Windows,
without storing a password. Each run's output is logged to `logs/` (gitignored)
via `Start-Transcript`. Check status with:

```powershell
Get-ScheduledTaskInfo -TaskName "BdeAnnivVirtChallenge-HourlyPipeline"
```

## Project layout

```
index.html                       generated dashboard; don't edit
requirements.txt
archive/                         pre-weekly activities.csv / members.csv, kept for reference
src/
  main.py                        pipeline entry point (scrape → generate → publish)
  login.py                       one-off manual Strava login → src/auth_state.json
  strava_session.py              shared Playwright session + CSV helpers
  config.py / config.yaml        settings (no secrets, no env vars)
  announcement.md                optional banner text
  auth_state.json                session cookies (gitignored; from AUTH_STATE)
  activities/
    activities.py                leaderboard + member profile weeks → weekly totals + activity ledger
    daily.csv                    Strava's week-to-date totals per member per day (committed; the dashboard's figures)
    activities.csv               best-effort activity ledger, for per-activity awards (committed)
  members/
    members.py                   headline count + feed/leaderboard athletes → append-only ledger
    members.csv                  member ledger (committed)
    member_count.json            Strava's headline member count (committed)
  nominal_roll/
    nominal_roll.py               raw FormSG export → cleaned roster
    nominal_roll.csv             roster (gitignored; from NOMINAL_ROLL)
  dashboard/
    generate.py                  load CSVs → compute → render → write index.html + src/user-count.json
    stats.py                     statistics engine (totals, awards, leaderboard)
    names.py                     NominalRoll, truncated-name resolution
    renderer.py                  token substitution into template.html
    weather.py                   Open-Meteo current weather (optional)
    template.html                dashboard markup, styles, and JS; edit this
  docs/                          README screenshots
test/                            pytest suite (unit / integration / e2e)
scripts/
  run_pipeline.ps1               Windows Task Scheduler entry point (hourly, local)
logs/                            run_pipeline.ps1 output (gitignored)
.github/workflows/deploy.yml     publish index.html to GitHub Pages on push
.github/workflows/test.yml       tests + coverage on every push/PR
```

## Contributing

You need Python 3.10 or newer (the code uses `X | None` annotations; CI pins
3.14), a Strava account in the club, and the club set to show member activity.

Run `python -m pytest test/ -q` (or scope to `test/unit`, `test/integration`,
`test/e2e`) from the repo root before you push — `test.yml` runs it in CI. `pytest` is in
`requirements.txt`; every fixture is synthetic, so the real roster and the real
FormSG export are never read.

## Troubleshooting

**"STRAVA RE-AUTH REQUIRED", or a scrape reports the session expired or was
blocked.** Run `python -m src.login`, re-encode `src/auth_state.json`, and update
the `AUTH_STATE` secret.

**"Member count not found".** Usually the same expired session. If the login is
fresh, Strava changed its members-page markup and the `membership-count` regex in
`src/members/members.py` needs updating.

**Everyone shows up with no unit, company, or full name.** `nominal_roll.csv` is
missing. Rebuild it locally with `nominal_roll`; in CI, check the
`NOMINAL_ROLL` secret.

**One runner's stats are missing or under the wrong unit.** Their Strava display
name doesn't match the `STRAVA username` column in the roll. Strava may truncate it
differently than the roll expects.

**No weather.** Check `weather.latitude` / `weather.longitude`. The widget is
optional and the dashboard works without it.

**Scheduled runs stopped.** GitHub disables cron on repos with no recent activity.
Re-enable it from the Actions tab.

---

## Licence

MIT.

Data from the [Strava API](https://developers.strava.com/) and weather from
[Open-Meteo](https://open-meteo.com/). Based on
[DatabenderSK/strava-club-dashboard](https://github.com/DatabenderSK/strava-club-dashboard).
