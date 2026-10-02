# `test/` — logic tests for `src/`

These tests pin down the **reasoning** in `src/` — the rules and thresholds —
not the current output byte-for-byte. A formatting change should break few or
no tests; changing an actual rule (a filter direction, a qualifying threshold,
a dedupe tie-break) should break a targeted one.

All fixture data is synthetic. The real `src/nominal_roll/nominal_roll.csv` and
the real FormSG export are never read.

## Running

From the repo root, with a Python that has the `src` deps (`pyyaml`,
`playwright`, `pytest`):

```
python -m pytest test/ -q
python -m pytest test/unit -q
python -m pytest test/integration -q
```

The project's uv `.venv` works once `pyyaml` is added (it is in
`requirements.txt` but was not installed): `uv pip install pyyaml`.

`test/conftest.py` puts the repo root on `sys.path` so `import src.…`
resolves; there is no `pytest.ini` / `pyproject.toml`.

## Layout

| Path | Covers |
|---|---|
| `unit/test_nominal_roll.py` | `parse_field`, `resolve`, `canon_company`, `dedupe`, `is_nil`, `smart_title`, `clean_service`, `entry_order` |
| `unit/test_stats.py` | `_num`, `AthleteStats` qualifiers, `compute_stats` (totals from weekly rows, awards from the ledger), every award threshold, `_device_sort` |
| `unit/test_names.py` | `_all_truncations`, `resolve`, `unit_company`, `service`, junk-company scrub, missing file |
| `unit/test_generate_helpers.py` | `day_label`, `_local_date` (timezone edges) |
| `unit/test_config.py` | `config.load()` per-key fallback |
| `unit/test_renderer.py` | `_slim_leaderboard`, `build_announcement_html`, `render` placeholder substitution |
| `unit/test_weather.py` | `weather_html` — network-optional degradation (`urllib.request.urlopen` mocked) |
| `unit/test_activities_parse.py` | `_text`, `parse_stats`, `to_meters/seconds/int`, `_row`, `normalise` (both feed schemas), `week_id`, `weeks_to_sync` (Monday grace, setup range), `parse_leaderboard`, `foot_rows`, `snapshot_date`, `cumulate` (on top of last week's row, Monday chain, leaderboard wins, earlier days kept) |
| `unit/test_members_parse.py` | `parse_member_count` |
| `integration/test_nominal_roll_convert.py` | `convert()` — synthetic FormSG CSV → roster file bytes + rules + missing-column abort |
| `integration/test_member_activities.py` | `member_activities.run` with a fake page: cumulative daily rows, ledger, leaderboard override, earlier days kept, expiry / mass-failure errors |
| `integration/test_recent_activities.py` | `fetch_count_and_feed`: headline count, feed cursor paging, error paths |
| `integration/test_dashboard_pipeline.py` | `load_activities` + `load_daily` + `load_members` + `latest_by_athlete` + `feed_updates` + `build_grouped_data` + `build_daily_history` wired together |
| `integration/test_scraper_write.py` | `append_activities`, `write_members`, `recent_activities.run` (append-only, dedupe by id; leaderboard athletes added) — no browser |
| `e2e/test_generate_end_to_end.py` | real `generate.run()` → `index.html`: placeholders filled, announcement wired, group/roster/history invariants |
| `e2e/test_roll_to_dashboard.py` | raw FormSG export → `convert()` → `nominal_roll.csv` → dashboard grouping matches the converted roll |
| `e2e/test_strava_parity.py` | **live, `--live` only**: `index.html` vs Strava — each leaderboard athlete's this/last-week figures and the member total |

The 6 tests from the former `src/dashboard/test_stats.py` are migrated into
`unit/test_stats.py` and `integration/test_dashboard_pipeline.py`.

## Live tests

Tests marked `live` hit the real Strava session and are skipped unless you pass
`--live`, so `src/auth_state.json` being present never triggers them by accident.
