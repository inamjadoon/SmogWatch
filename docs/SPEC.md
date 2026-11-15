# SmogWatch Punjab — Technical Specification

Companion to `PRD.md`. This document defines *how* each part works so any teammate (or Claude Code)
can build a module without guessing.

---

## 1. Architecture
```
NASA FIRMS (MODIS, VIIRS) ─┐
NASA POWER (weather) ──────┼─> fetch_*  ─> data/raw/  ─> harmonize ─> data/processed/*.parquet
NASA Earthdata (AOD, opt) ─┘                                  │
                                                              ├─> calendar  ─┐
                                                              ├─> exposure  ─┼─> Streamlit app (app/)
                                                              └─> model     ─┘
```
- **Offline pipeline** (`scripts/build_all.py`) produces Parquet files.
- **App** only reads Parquet (fast, no API calls at page load), except an optional "refresh last 48 h" button.

## 2. Repository layout
```
SmogWatch/
  CLAUDE.md  PLAN.md  README.md  requirements.txt  .env.example  .gitignore
  docs/  PRD.md  SPEC.md  DATA_SOURCES.md
  src/smogwatch/
    __init__.py  config.py  fetch_firms.py  harmonize.py  calendar.py
    weather.py   aerosol.py exposure.py     features.py  model.py
  scripts/build_all.py
  app/streamlit_app.py
  data/raw/        (gitignored)
  data/processed/  (committed if small, < 50 MB; else regenerate)
  tests/test_harmonize.py  tests/test_exposure.py
```

## 3. Configuration (`config.py`)
| Name | Value |
|---|---|
| `BBOX` | `(69.0, 27.5, 77.8, 33.0)` (W, S, E, N) |
| `LAHORE` | `(31.5497, 74.3436)` (lat, lon) |
| `MAIN_SEASON` | 15 Sep – 15 Dec |
| `SECOND_SEASON` | 1 Apr – 31 May |
| `YEARS` | 2012–2026 |
| `DEDUPE_EPS_KM` | 1.0 |
| `EXPOSURE_RADIUS_KM` | 300 |
| `UPWIND_HALF_ANGLE_DEG` | 45 |
| `SOURCES_SP` | `MODIS_SP, VIIRS_SNPP_SP, VIIRS_NOAA20_SP` |
| `SOURCES_NRT` | `MODIS_NRT, VIIRS_SNPP_NRT, VIIRS_NOAA20_NRT, VIIRS_NOAA21_NRT` |
| Secrets | `FIRMS_MAP_KEY` (+ optional `EARTHDATA_USERNAME`, `EARTHDATA_PASSWORD`) from `.env` / Streamlit Secrets |

## 4. Module specs

### 4.1 `fetch_firms.py`
- `fetch_area(source, bbox, start_date, day_range) -> pd.DataFrame`
- `fetch_season(source, year, season) -> pd.DataFrame` loops in `day_range` chunks.
- Endpoint (verify against live docs): `/api/area/csv/{KEY}/{SOURCE}/{W,S,E,N}/{DAY_RANGE}/{DATE}`
- Cache each chunk as `data/raw/{source}/{date}_{range}.csv`; skip if cached.
- Retry with backoff on HTTP 429/5xx; sleep between calls to respect the transaction limit.
- Use `_SP` sources for past years and `_NRT` for the last ~2 months (whichever the docs say covers it).

### 4.2 `harmonize.py`
**Input columns (raw FIRMS):**
- MODIS: `latitude, longitude, brightness, scan, track, acq_date, acq_time, satellite, instrument, confidence(0–100), version, bright_t31, frp, daynight`
- VIIRS: `latitude, longitude, bright_ti4, scan, track, acq_date, acq_time, satellite, instrument, confidence(l/n/h), version, bright_ti5, frp, daynight`

**Unified detection schema (`detections.parquet`):**
| Column | Type | Notes |
|---|---|---|
| `det_id` | str | hash of sensor+lat+lon+time |
| `event_time_utc` | datetime | from `acq_date` + `acq_time` (HHMM) |
| `date` | date | UTC date |
| `lat`, `lon` | float | degrees |
| `sensor` | str | `MODIS` / `VIIRS` |
| `satellite` | str | Terra, Aqua, N (SNPP), N20, N21 |
| `confidence_raw` | str | original value |
| `confidence_class` | str | `low` / `nominal` / `high` |
| `frp_mw` | float | fire radiative power, MW |
| `brightness_k` | float | MODIS `brightness` / VIIRS `bright_ti4` |
| `pixel_km2` | float | `scan * track` |
| `daynight` | str | D / N |
| `country` | str | PK / IN (point-in-polygon or source file) |

**Confidence mapping:** VIIRS `l→low, n→nominal, h→high`. MODIS `<30 low, 30–79 nominal, ≥80 high`.

**Filtering:** drop `low` confidence by default (keep a toggle); keep only points inside the bbox.

**Cross-sensor de-duplication → `fire_events.parquet`:**
- Group by `date`. Run DBSCAN with `metric="haversine"`, `eps = DEDUPE_EPS_KM / 6371`, `min_samples=1`
  on radians of (lat, lon).
- One cluster = one fire event. Aggregate:
  `event_id, date, lat/lon (FRP-weighted centroid), first_time_utc, n_detections, max_frp_mw, sum_frp_mw,
  best_confidence, seen_by (MODIS | VIIRS | both), satellites (list), country`.
- Rationale: MODIS and VIIRS overpasses on the same day see the same field fire at slightly different
  positions (1 km vs 375 m pixels); 1 km radius merges them without merging neighbouring farms too often.
  Sensitivity check: report event counts at eps = 0.5, 1.0, 2.0 km.

### 4.3 `calendar.py`
- `daily_counts(events, by=["date"], country=None) -> DataFrame[date, n_events, sum_frp]`
- `calendar_matrix(daily) -> pivot (year × day_of_year)`; fill missing days with 0.
- `season_stats(daily, year)`: `start` (first of 3 consecutive days > p25 of season), `peak_date`,
  `end`, `total_events`, `total_frp`, `peak_count`.
- `critical_days(daily)`: day count > 90th percentile of the same day-of-year (±7-day window) across prior years.
- `trends(stats)`: Mann-Kendall + Sen's slope on total events and peak day-of-year (`pymannkendall`).

### 4.4 `weather.py`
- NASA POWER daily point API, parameters `WS10M, WD10M, RH2M, T2M, PRECTOTCORR`, community `AG`, Lahore point.
- Output `weather_lahore.parquet`: `date, ws10m_ms, wd10m_deg, rh2m_pct, t2m_c, precip_mm`.
- `WD10M` is the direction the wind blows **from** (meteorological convention) — confirm in docs.

### 4.5 `exposure.py` — Lahore Smoke Exposure Index (LSEI)
For each date *d*:
1. Take fire events on *d* within `EXPOSURE_RADIUS_KM` of Lahore.
2. Bearing θ from Lahore to each fire. A fire is **upwind** if `|angle_diff(θ, wd10m)| ≤ 45°`.
3. Raw score `S = Σ_upwind frp_mw / max(distance_km, 10)`.
4. Stagnation multiplier `M = 1 + 0.5·[ws10m < 2 m/s] + 0.25·[rh2m > 70%]`.
5. `LSEI_raw = S · M`; `LSEI = 100 · clip(LSEI_raw / p99(LSEI_raw over history), 0, 1)`.
- Also output `lsei_upwind_events` and `lsei_top_district` for UI text.
- Unit tests: fire directly upwind > same fire downwind (= 0); closer fire > farther fire.

### 4.6 `aerosol.py` (optional, P2)
- Try NASA POWER aerosol parameter (e.g. `AOD_55`) if it exists; else MODIS `MOD08_D3` / `MYD08_D3`
  via `earthaccess`, variable `AOD_550_Dark_Target_Deep_Blue_Combined_Mean`, grid cell over Lahore.
- Output `aod_lahore.parquet: date, aod_550`. Report Pearson/Spearman r between LSEI and AOD at lags 0–3 days.

### 4.7 `features.py` / `model.py`
- Target: `n_events` for the full bbox at horizons h = 1..7.
- Features: lags 1–7, rolling mean/max (3, 7), `sin/cos(doy)`, `year`, `days_since_season_start`,
  weather (`precip_mm` lags 0–2, `rh2m`, `t2m`), last-year same-DOY count.
- Model: `LGBMRegressor` (objective `poisson` or `tweedie`), one per horizon.
- Split by time: train ≤ 2022, validate 2023, test 2024–2025.
- Baselines: persistence (`y_{t+h} = y_t`), seasonal naive (same DOY last year).
- Metrics: MAE, RMSE per horizon; table of model vs baselines saved to `model_metrics.json`.
- Inference: forecast next 7 days from latest date; forecast LSEI by combining forecast counts with the
  current season's average upwind share (clearly labelled as an estimate).

## 5. App spec (`app/streamlit_app.py`)
| Page | Contents |
|---|---|
| **Today** | pydeck map of last 48 h events (MODIS orange, VIIRS red, both purple); Lahore marker; wind arrow; KPI cards: events today, vs same date last year, LSEI today, 7-day risk |
| **Burning Calendar** | Plotly heatmap year × DOY; season stats table; critical-day markers; country + season filters |
| **Why Harmonize?** | Stacked bars MODIS-only / VIIRS-only / both per season; raw vs merged daily counts; eps sensitivity |
| **Forecast** | 7-day forecast line with history; metrics table vs baselines |
| **Smog Link** | LSEI time series; AOD overlay + r value (if available) |
| **About** | Problem, method, NASA datasets with links, limitations, team, AI-tool disclosure |

- Cache data loads with `@st.cache_data`.
- Colors colour-blind safe; every chart has a one-sentence takeaway caption.

## 6. Testing
- `pytest` for confidence mapping, de-duplication, exposure geometry, calendar fill.
- Sanity checks in `build_all.py`: no events outside bbox, no future dates, FRP ≥ 0, season peak in Oct–Nov for most years.

## 7. Deployment
- Streamlit Community Cloud from GitHub `main`, entry `app/streamlit_app.py`.
- Secrets: `FIRMS_MAP_KEY` in Streamlit Secrets (never in repo).
- Python version pinned in `runtime.txt` / Streamlit settings; dependencies in `requirements.txt`.

## 8. Known limitations (show in app)
- Detections are thermal anomalies, not confirmed crop fires; some may be industrial or urban.
- Cloud, thick smoke and overpass timing hide some fires; both sensors are polar-orbiting (few looks per day).
- LSEI is a transport proxy, not a dispersion model or PM2.5 forecast.
- Forecast is a prototype trained on ~14 seasons.
