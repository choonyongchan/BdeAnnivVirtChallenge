# `data/` — roster and ledgers

| File | What it is |
|---|---|
| `build_nominal_roll.py` | Converts the FormSG export into `nominal_roll.csv`. Run it from the repo root. |
| `nominal_roll.csv` | The roster the dashboard reads: name, unit, company, service type, Strava name. Generated — do not hand-edit. |
| `NOMINAL_ROLL_B64.txt` | The roster, base64-encoded, ready to paste into the GitHub secret. Generated. |
| `8th Brigade ... Registration Form-*.csv` | Raw FormSG export of the registration form. The source of truth. Never commit it (see [Privacy](#privacy)). |
| `../src/activity-ledger.json`, `../src/members-ledger.json` | Cached Strava activity and member data, written by the scheduled job (every 5 minutes). |

Both `nominal_roll.csv` and `NOMINAL_ROLL_B64.txt` are gitignored — they hold personal data
and are injected into CI from a GitHub secret instead.

## Regenerating the roster

**1. Export the form.** In FormSG, download the responses as CSV and drop the file into
`data/`. Keep the original filename.

**2. Run the converter** from the repo root:

```bash
python data/build_nominal_roll.py "data/<INSERT RAW NOMINAL ROLL>.csv"
```

This rewrites `data/nominal_roll.csv` and `data/NOMINAL_ROLL_B64.txt`, and prints a report:

```
INFO: TAN JIA HAO: unit "Keat Hong camp" backfilled from company "Glory" -> 41SAR
WARN: NG JUN RONG: unit "HQ1784" could not be normalised
--- 131 rows written, 9 flagged ---
```

`INFO` lines are corrections it made and is confident about. `WARN` lines need you.

**3. Fix every WARN at the source.** A flagged Unit is written to the CSV as the person typed
it, which means they will be missing from the dashboard's Unit and Company rankings. Correct the
answer in the form response itself, re-export, and re-run step 2. **Do not hand-edit
`nominal_roll.csv`** — the next run overwrites it.

If a WARN is actually a legitimate unit the script doesn't know yet, add it to
`UNIT_COMPANIES` in `build_nominal_roll.py` instead.

## Pushing it live

The dashboard does **not** read the file you just generated. `.github/workflows/update.yml`
recreates `data/nominal_roll.csv` in CI from the `NOMINAL_ROLL_B64` repository secret:

```yaml
echo "$NOMINAL_ROLL_B64" | base64 -d > data/nominal_roll.csv
```

So the last step is: open `data/NOMINAL_ROLL_B64.txt`, copy the whole line, and paste it into
**Settings → Secrets and variables → Actions → `NOMINAL_ROLL_B64`**.

Skip this and nothing breaks and nothing changes — the dashboard just keeps serving the old
roster until someone notices.

## What the converter cleans

**Unit** must come out as a number followed by capitals (`40SAR`), with `SBW` as the one
named exception. Spacing and case are normalised (`40 sar`, `41 Sar` → `40SAR`, `41SAR`), the
unit is pulled out of longer answers (`HQ 8 SAB`, `S2 Br 8SAB`, `Keat Hong camp 8sab` →
`8SAB`), and a bare number gets the default suffix (`41` → `41SAR`). A reversed spelling
(`SAR41`) matches no unit pattern and is recovered from the company instead — see the
backfill below.

**Company** is canonicalised against the unit it belongs to, because company names are
unit-exclusive:

| Unit | Companies |
|---|---|
| 40SAR | Archer, Braves, Cougar, Stallion, **Hercules** (HQ coy) |
| 41SAR | Falcon, Glory, Hawk, Shrike, **Heron** (HQ coy) |
| 8SAB, SBW | *none* — company is always left blank |

So `BRAVES`/`braves` → `Braves`, `ARCHER COY` → `Archer`, and `HQ`/`BN HQ` → `Hercules` for
40SAR or `Heron` for 41SAR. `Nil` becomes blank. Other units (412SAR, 489SAR…) keep whatever
was typed, tidied up.

That exclusivity is also the safety net: when the Unit field is unusable but the Company is
recognisable, the unit is **backfilled from the company** (`armour` + `hawk` → `41SAR`). Those
are the `INFO` lines.

**Type of service** drops the option number: `Option 1 NSF` → `NSF`.

Everyone is kept, including the few who answered "no" to having a Strava account. A missing
Strava name is only warned about — that person is in the roll but no activity will ever match
them.

## Privacy

The FormSG export contains **NRIC, mobile numbers and email addresses**. Only name, unit,
company, service type and Strava name are carried into `nominal_roll.csv`.

Nothing in `data/` is published — only `index.html` is deployed to GitHub Pages. The raw
export is gitignored; keep it that way, and don't force-add it.
