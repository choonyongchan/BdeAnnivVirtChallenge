# `test/` — logic tests for `src/`

These tests pin down the **reasoning** in `src/` — the rules and thresholds —
not the current output byte-for-byte. A formatting change should break few or
no tests; changing an actual rule (a filter direction, a qualifying threshold,
a dedupe tie-break) should break a targeted one.

All fixture data is synthetic. The real `src/nominal_roll/nominal_roll.csv` and
the real FormSG export are never read.

## Running

From the repo root, with a Python that has the `src` deps (`pyyaml`,
`requests`, `playwright`, `pytest`):

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
| `unit/test_stats.py` | `_num`, `AthleteStats` qualifiers, `compute_stats`, every award threshold, `_device_sort`, `_json_safe` |
| `unit/test_names.py` | `_all_truncations`, `resolve`, `unit_company`, `service`, junk-company scrub, missing file |
| `unit/test_generate_helpers.py` | `day_label`, `_local_date` (timezone edges), `_zone` |
| `unit/test_config.py` | `config.load()` per-key fallback + `generate.load_config()` announcement-path resolution |
| `unit/test_renderer.py` | `_slim_leaderboard`, `build_announcement_html`, `render` placeholder substitution |
| `unit/test_weather.py` | `fetch_weather` / `weather_html` — network-optional degradation (`requests` mocked) |
| `unit/test_activities_parse.py` | `_text`, `parse_stats`, `to_meters/seconds/int`, `_row`, `normalise` (both feed schemas) |
| `unit/test_members_parse.py` | `parse_members` |
| `integration/test_nominal_roll_convert.py` | `convert()` — synthetic FormSG CSV → roster file bytes + rules + missing-column abort |
| `integration/test_dashboard_pipeline.py` | `load_activities` + `load_members` + `build_grouped_data` + `build_daily_history` wired together |
| `integration/test_scraper_write.py` | `ActivityScraper.write` and `MemberScraper.write` (both append-only, dedupe by id) — no browser |
| `integration/test_scraper_retry.py` | `StravaScraper.scrape` auth-check + 3-attempt backoff + `ScrapeError` handling (`time.sleep` patched) |
| `e2e/test_generate_end_to_end.py` | real `generate.run()` → `index.html`: placeholders filled, announcement wired, group/roster/history invariants |
| `e2e/test_roll_to_dashboard.py` | raw FormSG export → `convert()` → `nominal_roll.csv` → dashboard grouping matches the converted roll |

The 6 tests from the former `src/dashboard/test_stats.py` are migrated into
`unit/test_stats.py` and `integration/test_dashboard_pipeline.py`.

## Not covered

- A `@live` real-browser scrape smoke test. `src/auth_state.json` exists on
  this machine, so a `skipif` guard would not skip it — it would hit the live
  Strava session. Run the scrapers directly (`python -m src.main`) to check that
  path.
