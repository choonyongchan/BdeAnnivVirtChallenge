# Developer Guide

How the dashboard is built, where each number comes from, and how to change it safely.
For using and operating the dashboard, see the [README](../README.md).

## Architecture

Strava switched off its public club API in 2026, and its remaining APIs may follow, so
everything is read from the pages a logged-in member sees. A Playwright browser opens
a club page with the saved session, and the scrapers `fetch()` from inside that page, so
requests carry the page's cookies and headers. There is no OAuth and no API token.

```
backend/   scrapes Strava into CSVs     (Playwright; python -m backend.main)
frontend/  CSVs -> one static page      (no Playwright; python -m frontend.generate)
shared/    used by both: config.py + config.yaml (settings), data.py (CSV paths, foot sports, CSV and timestamp helpers)
           public/  the site GitHub Pages serves: index.html, user-count.json, icons, manifest
docs/      this guide, README screenshots
test/      unit / integration / e2e (pytest; every fixture is synthetic)
scripts/   run_pipeline.ps1, the Windows Task Scheduler entry point
```

`frontend` reads settings and CSVs through `shared/`, never through a scraper, so building
the page never loads Playwright. Put code both sides need in `shared/`; keep it free of
Playwright.

## One concern, one source

| Concern | Strava page | When | Writes |
|---|---|---|---|
| Members | `/clubs/{id}/members` headline (`membership-count`) | hourly | `backend/members/member_count.csv` (one row a run) |
| | `/clubs/{id}/members?page=1..N` roster (`ul.list-athletes`, 30 a page) | hourly | `backend/members/members.csv` |
| Activities | `/clubs/{id}/feed?feed_type=club` JSON, cursor-paged | hourly | `backend/activities/activities.csv` |
| | `/athletes/{aid}/interval?interval=YYYYWW&interval_type=week` | nightly (23:45) | `activities.csv` gaps |
| Statistics | `/clubs/{id}/leaderboard`, this week and the Last Week tab | hourly | `backend/statistics/statistics.csv` |
| | profile weeks (same interval fetch) | nightly | `statistics.csv` |

`backend/main.py` runs, in order: members, feed, [profile scan], leaderboard + statistics,
generate; `scripts/run_pipeline.ps1` then commits and pushes. `shared/config.yaml`'s `schedule` sets which local hours each job runs
("*" = every hour). The first scrape failure stops the run before anything is published.
A rate-limited or error-heavy profile scan still saves what it found, then fails the run.

### What Strava gives you, measured 2026-10-07

- **Feed:** about 26 hours of club activity, whatever `num_entries` is. It is ordered by
  upload time, so late or backdated uploads still appear. Hourly runs leave ~25 h of slack.
- **Members page:** the full roster for an admin session. Admins are listed first on
  every page, then 30 members a page. Rows carry id, name and location, but no join date.
- **Leaderboard:** top 100 only, this week and last week, club sport only (Run/Walk/Hike).
  Time is moving time.
- **Profile week (`/interval`):** the week's activity list (`#interval-rides`, React props
  with `preFetchedEntries`). It also carries `#totals`, but those sum **every** sport
  (Crossfit time included) to 0.1 km and 1 min, with no activity count, so they are not used.
- **Private profiles** return no activity list; the feed and leaderboard are the only
  sources for them. "Only You" activities are visible nowhere.

## Data files

All CSVs are committed: they are the history. `auth_state.json`, `*_b64.txt` and the
nominal roll are gitignored and must never be committed.

**`members.csv`** `athlete_id, name, ingest_at, left_at`. `ingest_at` is when the roster
first listed the athlete; `left_at` is when they disappeared, cleared if they rejoin. The
roster only marks leavers when its size equals the headline; otherwise newcomers are
added and a warning is printed (a join or leave mid-walk shifts the pages). The file is
rewritten through a temp file so a crash can't lose `ingest_at` history.

**`member_count.csv`** `scraped_at, member_count`. The headline is the dashboard's
member total; per day it uses that day's last reading.

**`activities.csv`** one row per activity, any sport, deduped on `activity_id`, never
rewritten. `scraped_at` is when it was first seen. Rows found by a nightly scan share
that scan's `synced_at` stamp.

**`statistics.csv`** `athlete_id, date, distance_m, moving_time_s, elev_gain_m,
activities, source, synced_at`. Each row is that athlete's cumulative foot-sport totals
since `challenge_start`, as of `date`. Every run upserts rows, in this priority:

1. **leaderboard:** a week's figures are added to the athlete's last row before that
   week. The row is dated the week's Sunday, or today for the running week.
2. **profile** (nightly): the same, from the sum of the week's foot activities.
3. **feed** (fallback, everyone else): the athlete's latest row, plus ledger foot
   activities dated after that row, or scraped after it was synced (late uploads). The
   result is dated today.

Weeks with nothing seen write no row, so feed-only athletes keep counting. Rewriting last
week's Sunday row does not cascade to this week's rows; the nightly profile scan rebuilds
both weeks for every member. `last_profile_sync()` (the latest `profile` `synced_at`)
decides which members are new: members ingested after it get every week since
`challenge_start` scanned.

## The page

`frontend/generate.py` loads the CSVs (the ledger filtered to foot sports from
`challenge_start`). `stats.py` builds totals, awards, leaderboard and devices.
`names.py` (`NominalRoll`) resolves Strava display names to the roll's unit, company and
service. `renderer.py` substitutes everything into `template.html`; data is embedded as
JSON with `</` escaped. `frontend/public/index.html` is generated, so edit the template,
never the output.

**Known limitation: namesakes merge.** `stats.py` and the leaderboard key rows by the
resolved display name, not `athlete_id`. Two or more members with the same Strava
display name (on 2026-10-07: two each of "Enoch Tan", "Sean Yeo" and "Shawn Lim") show as
one leaderboard row, with their figures summed and attributed to one roll entry. Club
totals, statistics.csv and the CSVs stay per athlete. The live parity test skips these
athletes. Fixing it means keying the leaderboard by `athlete_id` and disambiguating the
displayed names.

Template rules:
- Any Strava-sourced text (names, units, companies, devices) goes through `esc()` before
  `innerHTML`. Strava names are attacker-controlled: any member can rename themselves.
- Clickable non-buttons (headers, calendar days, in-text links) get `tabindex`/`role`
  automatically from `makeKeyboardable()`. Real buttons are still preferred.
- Motion respects `prefers-reduced-motion`.
- Layout follows the screen's shape (the `LAYOUT` block at the end of the CSS):
  - **Narrow or portrait** (below 1200 px, or any portrait screen): leaderboard first, then
    one ranking at a time. The `#` and Runner columns stay pinned while the other columns
    scroll sideways.
  - **Wide landscape** (from 1200 px): the page widens to 1760 px. From 1680 px the
    rankings move into a sticky sidebar. Below 1680 px the leaderboard's ~1180 px of
    columns need the full width.
  - **Short landscape** (height up to 500 px, phones on their side): the nav no longer
    sticks, the controls share one line, and the totals sit in one row.

  `test_built_page_runs_in_a_browser_without_errors` checks four of these shapes.

To regenerate the icons after changing `favicon.svg`, render it with Playwright at 16,
32, 48 (packed into `favicon.ico` as PNG-in-ICO), 180, 192 and 512 px. No image library
is needed.

## Tests

```bash
.venv\Scripts\python -m pytest test/ -q                          # everything offline
.venv\Scripts\python -m pytest test/ --cov=backend --cov=frontend
.venv\Scripts\python -m pytest test/e2e/test_strava_parity.py --live   # vs live Strava
```

- `test/unit`: parsers (feed entries, roster HTML, headline, leaderboard rows),
  statistics rules, name resolution, rendering, config.
- `test/integration`: each scraper's `run()`/fetch with a fake Playwright page; CSV
  writers; the dashboard load-and-build chain.
- `test/e2e`: synthetic CSVs to a generated page, including the page opened in headless
  Chromium at 390 px and 1440 px (no script errors, no sideways overflow).
  `test_strava_parity.py --live` compares the dashboard's weekly deltas with Strava's
  leaderboard. Run it right after a full run; members who share a display name are
  skipped, because the dashboard merges namesakes by name.

CI (`.github/workflows/test.yml`) runs the offline suite on every push to `main`.

## Deploying

`scripts/run_pipeline.ps1` runs `backend.main`, then commits `statistics.csv`,
`activities.csv`, `members.csv`, `member_count.csv` and `frontend/public/` and pushes.
`.github/workflows/deploy.yml` publishes `frontend/public/` to GitHub Pages whenever it
changes (Settings, then Pages, then Source = GitHub Actions). Only tracked files are
published, so keep anything sensitive out of `frontend/public/`.

The scheduled run commits whatever is staged. Pause the task
(`schtasks /Change /TN "BdeAnnivVirtChallenge-HourlyPipeline" /DISABLE`) while working in
the same checkout.

## When Strava changes its markup

| Symptom | Where to look |
|---|---|
| "Member count not found" | `parse_member_count` in `backend/members/members.py` |
| Roster always short, warning every hour | `ROSTER_ROW` / `parse_roster` (same file) |
| "Feed response was not JSON" | `FEED_URL` in `backend/activities/feed.py` (or an expired session) |
| "Leaderboard empty" | `ROWS_JS` and the `span.button.last-week` tab in `backend/statistics/statistics.py` |
| Profile weeks all empty | `BATCH_JS` (the `#interval-rides` marker) in `backend/activities/member_activities.py` |

Probe the live page in a logged-in browser first, then update the parser and its
fixture-based test together.
