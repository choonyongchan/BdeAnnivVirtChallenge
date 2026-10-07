# `test/`: logic tests for `backend/` and `frontend/`

These tests pin down the **reasoning** in the code (the rules and thresholds), not
the current output byte-for-byte. A formatting change should break few or no tests;
changing an actual rule (a filter direction, a qualifying threshold, a dedupe
tie-break) should break a targeted one.

All fixture data is synthetic. The real `backend/nominal_roll/nominal_roll.csv` and
the real FormSG export are never read.

## Running

From the repo root, with the project venv (`pip install -r requirements.txt`):

```
python -m pytest test/ -q
python -m pytest test/ --cov=backend --cov=frontend
```

`test/conftest.py` puts the repo root on `sys.path` so `import backend.…`,
`import frontend.…` and `import shared.…` resolve; there is no `pytest.ini` / `pyproject.toml`.

## Layout

| Path | Covers |
|---|---|
| `unit/test_nominal_roll.py` | `parse_field`, `resolve`, `canon_company`, `dedupe`, `is_nil`, `smart_title`, `clean_service`, `entry_order` |
| `unit/test_stats.py` | `compute_stats` (totals, awards and thresholds), `_device_sort` |
| `unit/test_names.py` | `_all_truncations`, `resolve`, `unit_company`, `service`, junk-company scrub, missing file |
| `unit/test_generate_helpers.py` | `day_label`, `shared.data.local_date` (timezone edges, bad input) |
| `unit/test_config.py` | `shared.config.load()`: every key required |
| `unit/test_renderer.py` | `_slim_leaderboard`, `build_announcement_html`, `render` placeholder substitution |
| `unit/test_weather.py` | `weather_html` with the network mocked |
| `unit/test_activities_parse.py` | feed/profile entry parsing (`normalise`, both schemas), number coercion, `week_id`, `weeks_to_sync`, `own_rows`, `parse_leaderboard`, `snapshot_date`, `cumulate` |
| `unit/test_members_parse.py` | `parse_member_count`, `parse_roster` (admins + members lists only) |
| `unit/test_statistics.py` | `fetch_leaderboard` (both tabs, empty = error), ledger fallback filters, `last_profile_sync` |
| `unit/test_main.py` | `check_auth`, which jobs run at which hour, a short scan saving then failing |
| `integration/test_nominal_roll_convert.py` | `convert()`: synthetic FormSG CSV to roster file |
| `integration/test_feed_and_roster_fetch.py` | feed cursor paging and errors; roster paging until nobody new; members-page errors |
| `integration/test_member_activities.py` | profile scan with a fake page into the ledger and statistics: leaderboard wins, private profiles, leavers skipped, newcomers' full history, ledger fallback, 429 / error handling |
| `integration/test_dashboard_pipeline.py` | `load_activities`, `member_on`, headline counts by day, `build_grouped_data`, `build_daily_history` |
| `integration/test_scraper_write.py` | ledger append/dedupe, feed ledgers every sport, members upsert (`ingest_at`, `left_at`, rejoin, untallied roster), headline history |
| `e2e/test_generate_end_to_end.py` | real `generate.run()` to `index.html`: placeholders, invariants, headline count, and the page in headless Chromium at phone and desktop width |
| `e2e/test_roll_to_dashboard.py` | raw FormSG export to `convert()` to dashboard grouping |
| `e2e/test_strava_parity.py` | **live, `--live` only**: the page vs Strava's leaderboard and member headline |

## Live tests

Tests marked `live` hit the real Strava session and are skipped unless you pass
`--live`, so `backend/auth_state.json` being present never triggers them by accident.
