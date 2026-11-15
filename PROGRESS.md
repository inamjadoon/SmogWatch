# SmogWatch: Progress Log

A running record of each build step: what was done, decisions made, and any errors (with fixes).
Newest step at the bottom. Times are local (PKT).

---

## Step 1: Project scaffold (M0 setup), 15 Nov 2026

**Goal:** an importable Python package, a dependency list and config, so that later modules can be added one at a time.

### Done
- Created the folder layout: `src/smogwatch/`, `scripts/`, `app/`, `tests/`, `data/raw/`, `data/processed/` (with `.gitkeep`).
- `requirements.txt`: pandas, numpy, pyarrow, requests, python-dotenv, scikit-learn, lightgbm,
  pymannkendall, streamlit, plotly, pydeck, pytest, plus `-e .` to install the local package.
- `pyproject.toml`: makes `src/smogwatch` an installable package (`pip install -e .`). It also sets pytest
  `pythonpath = ["src"]` so tests import the package without any setup.
- `src/smogwatch/config.py`: bbox, Lahore coords, seasons, years, dedupe/exposure parameters, MODIS
  confidence thresholds, FIRMS + POWER endpoints/sources, and paths. It also has helpers:
  - `get_secret()`: reads `.env`, falling back to Streamlit Secrets
  - `firms_map_key()`: raises a clear error if the key is missing
  - `season_bounds()`
- `.gitignore`: ignores `.env`, `.venv`, caches and `data/raw/*`.
- `.env.example`: a template for `FIRMS_MAP_KEY` and the optional Earthdata login.
- `tests/test_config.py`: smoke tests (bbox ordered, Lahore inside bbox, season dates, paths).
- Created the `.venv` virtual environment and installed the requirements.

### Decisions / deviations
- **Python 3.13 instead of 3.11.** Python 3.11 is not installed on this machine (only 3.10 and 3.13). All
  dependencies have 3.13 wheels. Set Python 3.13 in Streamlit Cloud's "Advanced settings" when deploying.
- **`PLAN.md` does not exist yet.** The milestones follow the PRD priorities (F1–F6 first).

### Errors
- None. The install took over 10 minutes (it outran the tool timeout and finished in the background). Installed: pandas 3.1.1,
  lightgbm 4.7.0, streamlit 1.67.0. All imports OK.

---

## Step 2: FIRMS fetcher (F1), 15 Nov 2026

**Goal:** download MODIS + VIIRS detections for the bbox, cached and limit-safe.

### Done
- Checked the live FIRMS API docs (area + data_availability pages) before writing code.
- `src/smogwatch/fetch_firms.py`:
  - `area_url()`, `date_chunks()`, `parse_firms_csv()`, `cache_path()` are pure, tested helpers.
  - `fetch_area()` makes one request of up to 5 days. It caches to `data/raw/{SOURCE}/{start}_{range}.csv`, always
    re-fetches chunks within 3 days of today (NRT data still arriving), retries on 429/5xx with backoff,
    waits 60 s if the transaction limit is hit, and never caches FIRMS error text.
  - `data_availability()` + `plan_requests()`: for each satellite family, use the SP archive up to its last
    available date, then NRT. Days before a satellite existed are skipped (e.g. NOAA-21 before 2023).
  - `fetch_range()`, `fetch_period()`, `fetch_season()`, plus a CLI:
    `python -m smogwatch.fetch_firms --years 2025 --season main` / `--years 2012-2026 --season main second` / `--recent 10`
- `tests/test_fetch_firms.py`: 10 offline tests (fake HTTP session). **14/14 tests pass.**

### Corrections to the spec (from live docs)
- **DAY_RANGE is 1–5** (not open-ended). Added `config.FIRMS_MAX_DAY_RANGE = 5`.
- **`VIIRS_NOAA21_SP` exists.** SPEC.md listed SP sources without NOAA-21. Config now has 4 families × (SP, NRT).
- Request size: ~19 requests per family per main season → ~1,100 requests for all of 2012–2026 (well under
  the 5,000 per 10 min limit, but expect roughly 30–60 min of download time for the full history).

### Errors
- None so far. **Not yet tested against the live API**, because there is no `.env` with `FIRMS_MAP_KEY` on this machine.

### Live test (user ran it, 16:09 PKT)
`python -m smogwatch.fetch_firms --recent 5` downloaded **5,225 detections** for 11–15 Nov 2026:
MODIS_NRT 1,316, VIIRS_NOAA20_NRT 2,118 and VIIRS_NOAA21_NRT 1,791. **The live API works.**
- **Finding, not an error:** VIIRS_SNPP_NRT was skipped. FIRMS's availability table shows SNPP NRT ends on
  **2026-11-02**, so Suomi-NPP has sent no data since. The planner correctly skipped it.
- Availability at the time of the test: SP archives end 2026-07-31 (VIIRS) / 2026-08-31 (MODIS); NRT covers the rest.

---

## Step 3: Harmonization + de-duplication (F2), 15 Nov 2026

### Done
- `src/smogwatch/geo.py`: haversine distance, bearing, and Pakistan/India tagging.
  - Boundaries come from Natural Earth 1:10m admin-0 (public domain), downloaded once, cut to PK + IN within
    the bbox, and saved as `data/processed/boundaries_pk_in.geojson` (22 KB, so it can be committed).
  - Added the `shapely>=2.0` dependency.
- `src/smogwatch/harmonize.py`:
  - `load_raw()`: reads every cached CSV and drops duplicate rows from overlapping chunks.
  - `to_detections()`: unified schema per SPEC §4.2, plus `source` and `fire_type`.
  - Confidence mapping: MODIS <30 low / 30–79 nominal / ≥80 high; VIIRS l/n/h.
  - `build_events()`: per-day DBSCAN, haversine, eps 1 km → fire events with FRP-weighted centroid,
    `seen_by` (MODIS / VIIRS / both), `n_modis`, `n_viirs`, `max_frp_mw`, `sum_frp_mw`, `best_confidence`, `satellites` and `country`.
  - `eps_sensitivity()`: event counts at 0.5, 1 and 2 km.
  - CLI: `python -m smogwatch.harmonize` rebuilds `detections.parquet` + `fire_events.parquet`.
- `tests/test_harmonize.py`: 11 tests (confidence, time padding, schema, bbox, static-source filter,
  cross-sensor merge, no merge when far apart or on different days, low-confidence toggle, eps monotonic, geometry).
  **25/25 tests pass.**

### Decisions
- **`fire_type` filter:** SP archives have a `type` column (0 = vegetation fire, 2 = static/industrial source).
  Only type 0 is kept when it is known. NRT has no `type`, so those rows are kept. This drops brick kilns and factories in archive years.
- **Satellite comes from the source name**, not the CSV column (VIIRS codes differ between SP and NRT).
- **`acq_time` zero-padding:** NRT files contain times like `743` (= 07:43). They are padded to 4 digits before parsing.

### Real-data result (11–15 Nov 2026)
- Detections: 5,225 (VIIRS 3,909, MODIS 1,316); IN 3,004, PK 2,221. Low-confidence detections: 151.
- Fire events (confidence ≥ nominal, 1 km): **3,714**. VIIRS-only 2,554, MODIS-only 1,112, both **48**.
- eps sensitivity: 0.5 km → 4,051, 1 km → 3,714, 2 km → 3,114 events.
- Border check: Lahore and Kasur → PK; Amritsar and Ferozepur → IN.
- **Why so few "both":** Aqua MODIS now passes at about **10 UTC (15:00 PKT)** because its orbit has drifted, while VIIRS
  NOAA-20/21 passes at about **08 UTC (13:00 PKT)**. Short stubble fires seen by one sensor have often burned out before
  the other passes. Terra (morning pass) logged only 1 detection. The two sensors complement each other in **time** as
  well as resolution. 30% of fire events were seen only by MODIS, even though its pixels are coarser. This is the main
  evidence for the "Why Harmonize?" page.

### Errors
- None.

### Pushed to GitHub (user, 15 Nov)
The local branch was reset onto the remote "Added readme" commit. 7 commits were pushed. `CLAUDE.md` is excluded via
`.git/info/exclude`, so it stays local only. The `.parquet` files are held back until the full history is built.

---

## Step 4: History download + burning calendar (F4), 15 Nov 2026

### Done
- `fetch_firms` CLI: added `--families` (run satellite families in parallel) and descending year ranges
  (`--years 2026-2012`, newest season first).
- Started 4 parallel background downloads (main season, 2026→2012). Logs are in `logs/fetch_{FAMILY}.log`
  (`logs/` is gitignored). Speed: about 1 min per season per family.
- `src/smogwatch/calendar.py`:
  - `season_dates()`: in-season dates per year, never in the future.
  - `daily_counts()`: per-day events for ALL / PK / IN with FRP, raw detection count, and MODIS-only / VIIRS-only / both split.
    **Zero-fills only inside downloaded season windows**, so missing data is never shown as "no fires".
  - `aligned_doy()`: leap-aligned day of year (1 Nov = 305 in every year), so heatmap columns line up.
  - `calendar_matrix()`: year × day-of-year pivot (days never fetched stay NaN).
  - `season_stats()`: start / end, peak date + count, cumulative 10/50/90 % dates, totals, `complete` flag.
  - `critical_days()`: count > p90 of the same time of year (±7 days) in **prior years only** (no look-ahead),
    with at least 3 prior years required.
  - `trends()`: Mann-Kendall + Sen's slope on total events, total FRP, peak DOY and 50 % DOY (complete seasons only).
  - `same_date_comparison()`: season-to-date total vs the same date last year (for the Today KPI).
  - CLI `python -m smogwatch.calendar` writes `daily_counts.parquet`, `season_stats.parquet` and `trends.json`.
- `tests/test_calendar.py`: 8 tests. **33/33 tests pass.**

### Decisions
- **Season start/end threshold.** SPEC says "> p25 of season", which is undefined when p25 = 0. Instead: the p25 of **pooled**
  in-season daily counts across all years (minimum 1 event), applied to runs of 3 or more consecutive days. This
  threshold is comparable across years. Also added cumulative 10 / 50 / 90 % dates, which are less sensitive to noise and
  are used for the "is the season shifting?" trend.

### Errors (fixed)
- `aligned_doy` gave 29 Feb day 60 instead of 59. Fixed: leap years shift from doy ≥ 60. A test caught it.
- One test had the wrong expected value (1 Oct = day 274, not 275). The test was fixed.
- pandas 3 deprecation: `dt.dayofyear` → `dt.day_of_year`.
- The first download monitor reported "finished" falsely, because Git Bash `ps` doesn't show arguments.
  The downloads themselves were unaffected.
- **Crash: `time data "11/11/2026 1045" doesn't match format`.** `data/raw/MODIS_NRT/2026-11-11_5.csv` was modified
  at 16:27, after the download (16:09) and after a successful harmonize (16:13). Its dates had been rewritten as
  `11/11/2026`, which is what happens when Excel opens and saves a CSV. The file was redundant, because the new chunks `2026-11-09_5` and
  `2026-11-14_2` cover the same days, so it was deleted. **Hardening:** `load_raw` now skips (with a warning) any file whose
  `acq_date` isn't `YYYY-MM-DD` instead of crashing, and a test covers it.
  ⚠️ **Don't open `data/raw/*.csv` in Excel and save.** Use the Parquet outputs, or open the CSV read-only.
- **Performance: harmonize took more than 10 min on the full history.** cProfile showed 98% of the time in two per-cluster
  pandas lambdas (satellite list, majority country). Both were vectorized (`get_dummies` + groupby max;
  size + sort + drop_duplicates). One season dropped from **133 s to 2.2 s**. A new test checks the satellite list and majority country.
- FIRMS returned occasional HTTP 500s and read timeouts during the download. The retry/backoff handled them, and no data was lost.

### History download: complete (16:25 → about 16:58, 4 parallel processes)
- 160 MB of raw CSV, about 2.09 M rows. NOAA-20 starts in 2018 and NOAA-21 in 2023 (0 rows before), as expected.
- Full harmonize (about 5 min): **2,059,843 detections → 1,202,955 fire events** (VIIRS-only 1.00 M, MODIS-only
  117 k, both 84 k); IN 916 k, PK 287 k. 7 detections fall outside both country polygons (border slivers).
- eps sensitivity: 0.5 km → 1.47 M, 1 km → 1.20 M, 2 km → 0.81 M events.

### ⚠️ Key validity issue found and fixed: the number of satellites changes over time
Raw season totals jump from 65,848 (2017) to 107,354 (2018), which is exactly when NOAA-20 data starts. NOAA-21 adds more in 2023.
More satellites mean more overpasses, which means more fires caught. Comparing raw totals across years therefore measures the
**constellation** as well as the burning. On the all-satellite series, the trend test said "no trend".
**Fix:** `config.CORE_SATELLITES = (Terra, Aqua, SNPP)` covers all of 2012+. `harmonize` now also writes
`fire_events_core.parquet`. `calendar` builds both series (a `constellation` column = `all` / `core`), and
**`trends.json` uses core only**. The all-satellite series is kept for maps and daily activity, where more coverage helps.

### Calendar results (main season, 15 Sep – 15 Dec)
- Every season peaks in **early-to-mid November** (31 Oct – 18 Nov). The 50 % date is always 1–7 Nov.
- 170 critical days (all satellites, ALL region).
- **Trends 2012–2025, core constellation (Mann-Kendall, Sen's slope):**
  - Whole region: **decreasing**, −3,127 events/yr (p = 0.029). FRP is also decreasing (p = 0.021).
  - **India: decreasing**, −3,229 events/yr (p = 0.009). The 50 % date is moving later, +0.4 day/yr (p = 0.010).
  - **Pakistan: increasing**, +340 events/yr (p = 0.003). FRP is increasing (p < 0.001), and the season comes
    **earlier**: peak −1.0 day/yr (p = 0.046), 50 % date −1.2 day/yr (p = 0.003).
  - The core 2024–2025 seasons are the lowest on record (27 k and 23 k events vs 65–86 k in 2012–2021).

### Known caveats (to show in the app)
- **Suomi-NPP NRT stops on 2026-11-02**, so the core 2026 season is missing SNPP after that date. 2026 is incomplete
  and already excluded from trends. For the current season, use the all-satellite series.
- Aqua and Terra orbits have drifted since about 2022 (later / earlier overpasses), so MODIS detection timing isn't perfectly
  constant even within "core".
- The parquet sizes are detections 76 MB and fire_events 32 MB. They are **too big to commit** (GitHub warns at 50 MB and blocks at 100 MB).
  The app step needs small app-ready files (e.g. last-7-days events plus daily counts).

### Next step
Step 5: `weather.py` (NASA POWER daily weather for Lahore), then `exposure.py` (Lahore Smoke Exposure Index).

---

## Step 5: Weather, smoke index, app, one-command build (F3, F5, F6), 15 Nov 2026, about 22:10 PKT

Low on time, so the decision was to ship the MVP (F1–F6) and move the forecast (F7) to the roadmap.

### Done
- `src/smogwatch/weather.py`: NASA POWER daily point API for Lahore (checked live, no key needed). -999 becomes NaN.
  5,433 days; valid through **2026-11-12** (POWER lags about 3 days).
- `src/smogwatch/exposure.py`: Lahore Smoke Exposure Index per SPEC §4.5 (300 km radius, ±45° upwind of the WD10M
  "from" direction, FRP/distance, stagnation boost, p99 scaling to 0–100). Also reports the upwind fire count and the **Pakistan
  share of upwind FRP**. Uses each event's **max** FRP, so repeat overpasses aren't double-counted. 1,347 season days.
- `tests/test_exposure.py`: 8 tests (upwind > downwind = 0, closer > farther, beyond-radius ignored, stagnation ×1.75,
  missing wind → NaN, PK share, scaling bounds, POWER parsing). **43/43 tests pass.**
- `scripts/build_all.py`: one command (fetch recent + weather → harmonize → calendar → exposure → app data).
  Includes sanity checks (bbox, no future dates, FRP ≥ 0), which **passed on 1,202,955 events**. It writes the small app files
  `recent_events.parquet` (last 14 days, 0.5 MB), `lsei.parquet`, `weather_lahore.parquet` and `meta.json`.
- `app/streamlit_app.py`: five tabs. Today (map of the last 48 h, KPIs), Burning Calendar (heatmap + critical days,
  region / satellite-set / metric filters, season totals, trend sentence), Why Harmonize? (seen-by bars, raw vs
  merged, eps table, constellation note), Smog Link (LSEI + upwind fires, PK/IN share), About (method, data,
  limitations, AI disclosure). Okabe-Ito colour-blind-safe palette. Tested headlessly with `streamlit.testing`
  (**no exceptions**, all metrics render).
- README: run commands, key results, **changes from the spec**, roadmap and limitations updated.

### Errors (fixed)
- Streamlit 1.67 deprecates `use_container_width` → replaced with `width="stretch"`.
- The test-script print crashed on the Windows console encoding (cp1252), which is not an app bug. Re-ran with `PYTHONIOENCODING=utf-8`.

### Results shown in the app
- Today (15 Nov, partial UTC day): 901 events; 1,351 in the last 48 h. Season to date is −14 % vs the same date in 2025 (all
  satellites) or −27 % (core). LSEI on 12 Nov = 5/100.
- 2026: 55 % of upwind fire power reaching Lahore came from Pakistan.

### Not done (roadmap)
- F7 7-day forecast, F10 AOD validation, F11 CSV download, F12 Urdu.

---

## Step 6: Plain-language GUI for non-experts, 15 Nov 2026, about 22:30 PKT

### Done
- Header: a one-line question ("Where are farm fires burning, and is their smoke heading to Lahore?"), a plain intro, and a
  "New here?" guide to every tab.
- Every chart has a **"How to read this"** expander (what each axis, colour and mark means, plus what to notice).
- Jargon replaced: "fire events" → "fires", FRP → "fire intensity (MW)", constellation → "Fair comparison: same 3
  satellites every year" / "Maximum detail: all 5 satellites". The tab "Why Harmonize?" was renamed "Why Combine Satellites?".
- Today: KPI tooltips plus an auto-generated **"In short"** summary sentence.
- **Smoke score bands** set from history (share of 2012–2026 season days): Low < 5 (about 76 %), Moderate 5–25
  (about 19 %), High 25–50 (about 2 %), Very high ≥ 50 (about 3 %). The bars are coloured by band, with a legend and the season's 3 smokiest days.
- Calendar: the trend is shown as a plain sentence ("📉 burning is falling by about 3,127 fires per year … statistically
  reliable"). The season table has friendly column names.
- About: a **glossary** (fire, detection, MODIS, VIIRS, FRP, upwind, smoke score, critical day, stubble burning, trend).

### Errors (fixed)
- Streamlit's test harness (`AppTest`) can't drive selectboxes that use `format_func` (`ValueError: None is not in list`).
  The dropdowns now use plain labels mapped to keys, which is also simpler code.
- The score card showed "5 / 100 · Moderate" because the band used the raw 5.2. The band now uses the rounded value shown.

### Verified
- Headless run of **all 12 calendar filter combinations + all 15 seasons = 27 runs, 0 exceptions.**

---

## Step 7: Cleaner look + interactive controls, 15 Nov 2026

### Done
- Removed the decorative emoji from the title, tabs, expanders, captions, trend sentence and page icon. The ✕ is kept
  because it is the actual critical-day marker on the calendar.
- **Today:** a "Show fires from" picker (last 2 / 7 / 14 days or any single day), plus "Seen by" and "Country" filters.
  The map and legend counts follow the filters. Added a "Fires per day, last 14 days" bar chart (Pakistan vs India).
- **Calendar:** a "Compare years" chart. Pick any seasons and they are overlaid as 7-day-smoothed curves on a
  shared date axis, with a unified hover; legend clicks hide or isolate years.
- **Smog Link:** "Look up a day" slider. For the chosen day it shows the smoke score + level, the wind direction in compass words
  and its speed, upwind fires, humidity, and a plain sentence including the Pakistan/India share. It defaults to the
  season's smokiest day.

### Errors (fixed)
- `select_slider` with `format_func` breaks Streamlit's `AppTest` serialization (`Could not serialize value`).
  Switched to plain string labels mapped to dates, the same fix as the selectboxes in step 6.

### Verified
- Headless: every period option, empty and single filters, compare-years empty and all, every season × both slider ends
  = **68 runs, 0 exceptions.**
