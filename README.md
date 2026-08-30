# 8SAB 50th Anniversary Virtual Challenge — Strava Dashboard

**→ [View the live dashboard](https://choonyongchan.github.io/BdeAnnivVirtChallenge/)**

A Strava-powered fitness dashboard for the **8SAB 50th Anniversary Virtual Challenge**. It pulls
activity data from a Strava club, matches members against a unit nominal roll, and publishes a live
leaderboard — broken down by unit and company — as a static site on GitHub Pages. GitHub Actions
regenerates it every 5 minutes.

![The dashboard: totals, awards and fun stats](docs/dashboard-overview.png)

**What's on the dashboard:**

- **Totals** — distance, elevation, activities, active runners, registered runners
- **Awards** — Distance King, Climbing King, Marathoner, Fastest, Longest Run, Mountain Goat, Flat Runner
- **Fun stats** — Break King
- **Runner leaderboard** with sortable columns and unit/company filters
- **Unit Rankings** and **Company Rankings**, driven by the nominal roll
- **Group tabs** — **All**, **NSF/Regular**, or **NSMan/Alumni**
- **History** — any past date, via a calendar picker
- **Trend** — cumulative charts, a weekly Sunday snapshot table, and per-unit/company runner charts
- Device breakdown, local weather, a dismissible announcement banner
- Fully responsive; no database, no server — one self-contained HTML file

---

## Contents

- [How the dashboard is organized](#how-the-dashboard-is-organized)
- [Maintaining the roster](#maintaining-the-roster)
- [How it updates](#how-it-updates)
- [Running it locally](#running-it-locally)
- [Deploying your own copy](#deploying-your-own-copy)
- [Getting or rotating a Strava refresh token](#getting-or-rotating-a-strava-refresh-token)
- [The announcement banner](#the-announcement-banner)
- [File structure](#file-structure)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

---

## How the dashboard is organized

Members are grouped two ways:

- **Unit / Company** — every athlete is looked up against `data/nominal_roll.csv` and shown with
  their formal name, unit, and company. This roll also powers the Unit and Company Rankings.
- **NSF/Regular vs NSMan/Alumni** — members are bucketed by their `Type of service` in the roll,
  configured via `SERVING_TYPES` / `ALUMNI_TYPES` in `src/generate.py`:
  - **NSF/Regular**: `Type of service` is `NSF` or `REGULAR`
  - **NSMan/Alumni**: `Type of service` is `NSman` or `Alumni`
  - Anyone off the roll, or with no recognised `Type of service`, appears only in **All**

Strava often truncates a member's display name (e.g. `"Siva R."`). `src/nominal_roll.py` matches
these truncated forms back to the full name in the roll so stats land on the right person.

![Runner leaderboard with unit and company columns](docs/dashboard-leaderboard.png)

### Maintaining the roster

> [!IMPORTANT]
> **Do not hand-edit `data/nominal_roll.csv`.** It is generated from the FormSG registration export
> and is overwritten on every run of the converter. It is also gitignored — it holds personal data
> and reaches CI through the `NOMINAL_ROLL_B64` secret, not the repo.

To add, remove, or re-assign a member, correct the response in the registration form, then
re-run the converter. The full procedure — including what it auto-corrects, how to read its
`INFO`/`WARN` output, and how to push the result live — is in **[`data/README.md`](data/README.md)**.

---

## How it updates

```
Strava API
    ↓
src/strava_client.py      → OAuth token refresh + fetch club activities/members
    ↓
src/ledger_generator.py   → Append to the activity + member ledgers (the history store)
    ↓
src/nominal_roll.py       → Match Strava names to the roster (unit/company)
    ↓
src/report_generator.py   → Compute statistics, awards, leaderboard
    ↓
src/generate.py           → Build grouped data, render the HTML template
    ↓
index.html                → Static file, published to GitHub Pages
```

`.github/workflows/update.yml` runs this pipeline:

- **On a schedule** — every 5 minutes (`*/5 * * * *`), GitHub's minimum cron granularity. Strava's
  club feed only exposes one page of recent activities, so a short interval keeps activities from
  overflowing out of view before they are recorded.
- **On demand** — **Actions → Update and Deploy Strava Dashboard → Run workflow**
- **On every push to `main`**

Only `index.html` is deployed to GitHub Pages. The roster and the ledger JSON files stay in the
repository and are never published.

**Key design decisions:**

- **No server, no database** — the output is a single self-contained HTML file.
- **Append-only ledgers** — every activity ever fetched is appended to `src/activity-ledger.json`
  and deduped into `src/activity-ledger-clean.json`. Strava's club feed carries no activity id or
  timestamp, so the ledger's `ingested_at` (scrape time) is the only timeline signal; per-date
  history is replayed from it at generation time.
- **Payload kept small** — historical snapshots omit the all-zero fields of members who hadn't run
  yet and rebuild them in the browser, which roughly halves the page.

![Trend view: weekly snapshot table and cumulative charts](docs/dashboard-trend.png)

---

## Running it locally

```bash
git clone https://github.com/choonyongchan/BdeAnnivVirtChallenge.git
cd BdeAnnivVirtChallenge
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
# Required
STRAVA_CLIENT_ID=your_id
STRAVA_CLIENT_SECRET=your_secret
STRAVA_REFRESH_TOKEN=your_token
STRAVA_CLUB_ID=your_club_id

# Optional — these are the defaults
CLUB_NAME=8SAB 50th Anniversary Virtual Challenge
WEATHER_LAT=1.3835
WEATHER_LON=103.7478
TIMEZONE=Asia/Singapore
START_DATE=            # YYYY-MM-DD; if set, ignores activities ingested before this date
```

Only the four Strava values are required. `.env` is for local runs only — GitHub Actions reads the
same names from repository secrets.

You also need a roster at `data/nominal_roll.csv` before the first run. Without it the dashboard
still generates, but nobody gets a unit, company, or full name — see
[`data/README.md`](data/README.md) to create it.

Then generate and open the result:

```bash
python3 src/generate.py
```

```
Fetching data from Strava...
  Fetching latest club activities...
  Anchor found: 8/8 of last 8 ledger entries matched, cutting at fresh[4].
  4 new activities appended to ledger.
  11 new members registered.
Generated: .../index.html (1.28 MB)
  Cumulative: 526 activities, 610 runners
```

`index.html` is entirely self-contained — no build step, no local server. Just open it in a browser.

> `index.html` is generated output. To change the dashboard's markup, styles, or JavaScript, edit
> the `TEMPLATE` string in `src/generate.py` and regenerate; edits to `index.html` are overwritten.

---

## Deploying your own copy

1. **Enable Pages**: **Settings → Pages → Source: GitHub Actions**. The workflow's `deploy` job
   fails without this.
2. **Add the secrets** under **Settings → Secrets and variables → Actions**:

   | Secret | Required | Purpose |
   |---|---|---|
   | `STRAVA_CLIENT_ID` | yes | Strava API app |
   | `STRAVA_CLIENT_SECRET` | yes | Strava API app |
   | `STRAVA_REFRESH_TOKEN` | yes | See [token rotation](#getting-or-rotating-a-strava-refresh-token) |
   | `STRAVA_CLUB_ID` | yes | The club to report on |
   | `NOMINAL_ROLL_B64` | yes | The roster, base64-encoded. CI decodes it to `data/nominal_roll.csv`. Without it, no member gets a unit or full name. See [`data/README.md`](data/README.md) |
   | `CLUB_NAME` | no | Dashboard title |
   | `WEATHER_LAT` / `WEATHER_LON` | no | Weather widget location |
   | `TIMEZONE` | no | IANA name, e.g. `Asia/Singapore` |
   | `START_DATE` | no | Ignore activities ingested before this date |

3. Push to `main`, or trigger the workflow manually.

---

## Getting or rotating a Strava refresh token

Strava refresh tokens can expire or be revoked. To get a new one:

```bash
python3 src/setup_strava.py
```

The wizard will:

1. Ask for the Client ID and Client Secret (from [strava.com/settings/api](https://www.strava.com/settings/api))
2. Give you a URL to open in your browser
3. You authorize the app on Strava
4. Your browser redirects to `localhost` (the page won't load — that's expected)
5. Copy the full redirected URL and paste it back into the wizard
6. It prints a new `STRAVA_REFRESH_TOKEN`

Update `.env` (local) and the `STRAVA_REFRESH_TOKEN` secret (deployed) with the new value.

---

## The announcement banner

Create `data/announcement.md` to show a dismissible banner at the top of the dashboard:

```markdown
# Welcome Commander!
This live data exist for testing purposes. Data will be refreshed on 14th Sept 00:00.
```

The first line is the title (leading `#` is stripped); everything after is the body. Delete the
file, or leave it empty, to hide the banner. Content is HTML-escaped.

---

## File structure

```
├── index.html                     # OUTPUT — the dashboard (generated; don't edit)
├── docs/                          # README screenshots
├── src/
│   ├── generate.py                # Entry point: builds grouped data + the HTML template
│   ├── strava_client.py           # Strava API client (OAuth + data fetch)
│   ├── ledger_generator.py        # Append-only activity and member ledgers
│   ├── report_generator.py        # Statistics engine (leaderboard, awards)
│   ├── nominal_roll.py            # Matches Strava names to the roster
│   ├── config.py                  # Configuration from .env / GitHub Secrets
│   ├── setup_strava.py            # OAuth setup / token-rotation wizard
│   ├── activity-ledger.json       # OUTPUT — raw activity log (source of truth)
│   ├── activity-ledger-clean.json # OUTPUT — deduped activity log, used for all stats
│   └── members-ledger.json        # OUTPUT — members + first-seen date
├── data/
│   ├── README.md                  # How to build and publish the roster — read this first
│   ├── build_nominal_roll.py      # FormSG export  →  nominal_roll.csv
│   ├── test_build_nominal_roll.py # Tests for the converter
│   ├── announcement.md            # Optional banner text
│   ├── nominal_roll.csv           # Roster (gitignored — personal data)
│   └── NOMINAL_ROLL_B64.txt       # Roster, base64 for the secret (gitignored)
├── requirements.txt
└── .github/workflows/
    └── update.yml                 # Update + deploy, every 5 minutes
```

---

## Development

**Requirements**

- Python 3.10+ (CI runs 3.14). 3.10 is the floor because the code uses `X | None` type syntax.
- A Strava account that is a member of the club
- The club must allow member activity visibility

**Tests** cover the roster converter. `pytest` is a development-only dependency and is deliberately
not in `requirements.txt`, so install it separately:

```bash
pip install pytest
pytest data/test_build_nominal_roll.py
```

Note that CI runs no test step — the scheduled workflow only generates and deploys.

---

## Troubleshooting

**"STRAVA AUTH ERROR: refresh token is likely expired"**
→ Run `python3 src/setup_strava.py` for a new token, then update `.env` and/or the
`STRAVA_REFRESH_TOKEN` secret.

**"ERROR: Missing required config"**
→ One of the four required variables is unset. Check `.env` locally, or repository secrets for the
deployed workflow. Note the script exits with status 0 so a misconfigured run does not show as a
failed workflow — check the job log, not just the badge.

**"401 Unauthorized" from Strava**
→ The Client ID or Client Secret is wrong. Verify at
[strava.com/settings/api](https://www.strava.com/settings/api).

**Everyone shows up without a unit, company, or full name**
→ `data/nominal_roll.csv` is missing. Locally, generate it per [`data/README.md`](data/README.md);
in CI, check the `NOMINAL_ROLL_B64` secret is set.

**One member's stats are missing or under the wrong unit**
→ Their Strava display name doesn't match the `STRAVA username` column in the roll. Strava can
truncate names differently than expected.

**"WARNING: ledger anchor not found"**
→ Strava's club feed had no overlap with the stored ledger, so the whole page was appended and a
few activities may be double-counted. Usually means the job hadn't run for a while.

**Weather not showing**
→ Check `WEATHER_LAT` / `WEATHER_LON`. Weather is optional; the dashboard works without it.

**GitHub Actions not running**
→ Check the **Actions** tab — scheduled workflows can be disabled automatically on repositories
with no recent activity.

---

## License

MIT.

---

Built with data from the [Strava API](https://developers.strava.com/) and weather from
[Open-Meteo](https://open-meteo.com/). Based on
[DatabenderSK/strava-club-dashboard](https://github.com/DatabenderSK/strava-club-dashboard).
