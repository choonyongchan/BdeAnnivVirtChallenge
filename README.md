# 8SAB 50th Anniversary Virtual Challenge — Strava Dashboard

A Strava-powered fitness dashboard built for the **8SAB 50th Anniversary Virtual Challenge**. It pulls activity data from a Strava club, matches members against a unit nominal roll, and publishes a live leaderboard — broken down by unit and company — as a static site on GitHub Pages. It auto-updates every hour via GitHub Actions.

**What's on the dashboard:**
- Leaderboard with 20+ metrics (distance, elevation, speed, time...)
- Awards: Distance King, Climbing King, Marathoner, Fastest, Mountain Goat, Flat Runner...
- Fun stats: Break King
- Device stats across the club
- **Unit Rankings** and **Company Rankings**, driven by the nominal roll
- **Group tabs** — view stats for **All** members, or split by **NSF** / **NSMen**
- History archive with a visual date picker
- Local weather widget (Singapore, by default)
- Fully responsive (mobile + desktop)
- No database needed — plain JSON file storage

---

## How the dashboard is organized

Members are grouped two ways:

- **Unit / Company** — every athlete is looked up against `data/nominal_roll.csv` and shown with their formal name, unit, and company. This roll is also what powers the Unit Rankings and Company Rankings tables.
- **NSF / NSMen** — units are additionally bucketed into two dashboard-wide groups, configured via `NSF_UNITS` in `src/generate.py`:
  - **NSF**: roll members whose `Unit` is `8SAB`, `40SAR`, or `41SAR`.
  - **NSMen**: roll members whose `Unit` is anything else.
  - Roll members with no `Unit` on file appear only in **All**, not in either group.

  Switch between the **All / NSF / NSMen** tabs on the dashboard to filter every section (leaderboard, awards, rankings) to that group.

Strava often truncates a member's display name (e.g. `"Siva R."`). `src/nominal_roll.py` matches these truncated forms back to the full name in the roll so stats are attributed to the right person.

### Maintaining the roster

To add, remove, or re-assign a member, edit `data/nominal_roll.csv`. Each row is:

```
Name,Unit,Company,Type of service,STRAVA username
Tan Ah Kau,40SAR,Hercules,REGULAR,Tan Ah Kau
```

- `STRAVA username` should match the member's display name on Strava (as it appears in the club's activity feed) so the dashboard can match their activities.
- `Type of service` is `REGULAR` or `NSF` (informational — it does not drive the NSF/NSMen dashboard tabs; that's controlled by `NSF_UNITS` above).
- Changes take effect on the next dashboard generation (next scheduled run, or the next manual trigger — see below).

---

## How it updates

```
Strava API
    ↓
src/strava_client.py      → OAuth token refresh + fetch club activities/members
    ↓
src/nominal_roll.py       → Match Strava names to the roster (unit/company)
    ↓
src/report_generator.py   → Compute statistics, awards, leaderboard
    ↓
src/generate.py           → Build grouped (all/nsf/nsmen) data, render HTML template
    ↓
index.html                → Static file, published to GitHub Pages
src/activity-ledger.json / activity-ledger-clean.json → activity log (source of truth for history)
```

`.github/workflows/update.yml` runs this pipeline:
- **On a schedule** — every hour (`0 * * * *`)
- **On demand** — via **Actions → Update Strava Dashboard → Run workflow**
- **On every push to `main`**

Only `index.html` is published to GitHub Pages — the roster (`data/nominal_roll.csv`) and ledger files stay in the repo but are not deployed publicly.

**Key design decisions:**
- **No server needed** — generates a single static HTML file
- **No database** — every activity ever fetched is appended to `src/activity-ledger.json`, deduped into `src/activity-ledger-clean.json`. History (per-date leaderboards) is computed on the fly from this ledger at generation time
- **E-bike fair play** — awards exclude e-bike rides (tracked separately as a fun stat)
- **Rate limit friendly** — a handful of API calls per run (Strava allows 1000/day)

---

## Running it locally

```bash
git clone https://github.com/choonyongchan/BdeAnnivVirtChallenge.git
cd BdeAnnivVirtChallenge
pip install -r requirements.txt
```

Create a `.env` file in the project root with:

```env
STRAVA_CLIENT_ID=your_id
STRAVA_CLIENT_SECRET=your_secret
STRAVA_REFRESH_TOKEN=your_token
STRAVA_CLUB_ID=your_club_id
CLUB_NAME=8SAB 50th Anniversary Virtual Challenge
WEATHER_LAT=1.352083
WEATHER_LON=103.819839
TIMEZONE=Asia/Singapore
```

Only the first four (`STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REFRESH_TOKEN`, `STRAVA_CLUB_ID`) are required; the rest have sensible defaults. `.env` is for local runs only — the deployed GitHub Actions workflow reads the same variable names from **GitHub Secrets** instead (Settings → Secrets and variables → Actions).

Generate and preview:

```bash
python3 src/generate.py
```

Then open `index.html` in your browser.

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

Update `.env` (local) and the `STRAVA_REFRESH_TOKEN` GitHub Secret (deployed) with the new value.

---

## File Structure

```
├── index.html                 # OUTPUT — the dashboard (don't edit manually!)
├── src/
│   ├── generate.py            # Main script — builds grouped data, generates the dashboard
│   ├── strava_client.py       # Strava API client (OAuth + data fetch)
│   ├── report_generator.py    # Statistics engine (all computations)
│   ├── nominal_roll.py        # Matches Strava names to the roster
│   ├── config.py              # Configuration from .env / GitHub Secrets
│   ├── setup_strava.py        # OAuth setup/token-rotation wizard
│   ├── activity-ledger.json       # OUTPUT — raw activity log (source of truth)
│   └── activity-ledger-clean.json # OUTPUT — deduped activity log, used for all stats
├── data/
│   └── nominal_roll.csv       # Roster: name, unit, company, service type, Strava username
├── requirements.txt           # Python dependencies
└── .github/workflows/
    └── update.yml              # Hourly auto-update via GitHub Actions
```

---

## Requirements

- Python 3.9+ (CI runs Python 3.14)
- A Strava account that's a member of the club
- The club must be set to allow member activity visibility

---

## Troubleshooting

**"STRAVA AUTH ERROR: refresh token is likely expired"**
→ Run `python3 src/setup_strava.py` again to get a new token, then update `.env` and/or the `STRAVA_REFRESH_TOKEN` GitHub Secret.

**"ERROR: Missing required config"**
→ One or more required variables are missing. Check that `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_REFRESH_TOKEN`, and `STRAVA_CLUB_ID` are all set (in `.env` locally, or GitHub Secrets for the deployed workflow).

**"401 Unauthorized" from Strava**
→ The Client ID or Client Secret is incorrect. Double-check them at [strava.com/settings/api](https://www.strava.com/settings/api).

**A member's stats aren't showing up / show under the wrong unit**
→ Check that their Strava display name matches the `STRAVA username` column in `data/nominal_roll.csv`. Strava can truncate names differently than expected.

**Weather not showing**
→ Check `WEATHER_LAT`/`WEATHER_LON`. Weather is optional — the dashboard works without it.

**GitHub Actions not running**
→ Check the **Actions** tab — workflows can be disabled by default depending on repo settings.

---

## License

MIT.

---

Built with data from the [Strava API](https://developers.strava.com/) and weather from [Open-Meteo](https://open-meteo.com/). Based on [DatabenderSK/strava-club-dashboard](https://github.com/DatabenderSK/strava-club-dashboard).
