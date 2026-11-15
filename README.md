# SmogWatch

**Merging NASA MODIS and VIIRS fire data into a crop-burning calendar, a 7-day burning forecast, and a
smoke-exposure index for Lahore.**

Built for the **NASA Space Apps Challenge 2026**, challenge:
**Harmonization of MODIS and VIIRS Hot Spots**.

>  **Live app:** _coming soon_ ·  **Demo:** _coming soon_ ·  **Space Apps project page:** _coming soon_

---

## Table of contents
- [The problem](#the-problem)
- [What SmogWatch does](#what-smogwatch-does)
- [Why harmonize MODIS and VIIRS?](#why-harmonize-modis-and-viirs)
- [NASA data used](#nasa-data-used)
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [Running the pipeline and app](#running-the-pipeline-and-app)
- [Deployment](#deployment)
- [Roadmap](#roadmap)
- [Limitations](#limitations)
- [Team](#team)
- [Acknowledgements](#acknowledgements)
- [License](#license)

---

## The problem
Every year from **October to December**, farmers across **Punjab (Pakistan and India) and Haryana** burn
leftover rice stubble to clear their fields quickly for wheat sowing. Combined with traffic and industrial
emissions, cold air and low wind, that smoke pushes **Lahore's** air quality to some of the worst levels in
the world, closing schools, filling hospitals and delaying flights.

NASA satellites see these fires from space, but its two fire-detecting instruments describe them
differently, and existing maps show *where* fires are, not *which fires are affecting a city today*.

## What SmogWatch does
| Feature | Description |
|---|---|
|  **Merged fire map** | Every fire in the last 48 h, combined from MODIS and VIIRS with duplicates removed, colored by which sensor saw it |
|  **Burning activity calendar** | Heatmap of fires for each day of each year (2012→2026): season start, peak and end, critical days, Pakistan vs India |
|  **Lahore Smoke Exposure Index** | A 0–100 daily score using NASA wind data to measure how much fire activity is **upwind** of Lahore |
|  **7-day forecast** | Machine-learning forecast of fire activity, compared against simple baselines |
|  **Why harmonize?** | Shows how many fires only one sensor caught, which is the evidence that merging matters |

## Why harmonize MODIS and VIIRS?
| | MODIS | VIIRS |
|---|---|---|
| Satellites | Terra, Aqua | Suomi NPP, NOAA-20, NOAA-21 |
| Record starts | 2000 | 2012 |
| Pixel size | ~1 km | 375 m |
| Small farm fires | Often missed | Usually detected |
| Confidence | 0–100 | low / nominal / high |

The same fire can appear two or three times across sensors, or in only one of them. Raw counts are
therefore misleading. SmogWatch puts both onto **one schema**, maps confidence to a **common scale**, and
merges detections of the same fire into **one fire event** (spatial clustering per day, ~1 km radius).

## NASA data used
| Dataset | Provider | Used for |
|---|---|---|
| [FIRMS](https://firms.modaps.eosdis.nasa.gov/): MODIS Active Fire C6.1 | NASA LANCE / EOSDIS | Fire detections |
| [FIRMS](https://firms.modaps.eosdis.nasa.gov/): VIIRS 375 m Active Fire (SNPP, NOAA-20, NOAA-21) | NASA / NOAA | Fire detections |
| [NASA POWER](https://power.larc.nasa.gov/) daily | NASA LaRC | Wind, humidity, temperature, rain |
| MODIS Aerosol (MOD08_D3 / MYD08_D3) *(optional)* | NASA Earthdata | Validating the smoke index against haze |

Full details and sign-up links: [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md)

## How it works
```
NASA FIRMS (MODIS + VIIRS) ─┐
NASA POWER (weather) ───────┼─> fetch ─> harmonize ─> fire events ─┬─> burning calendar ─┐
NASA Earthdata (AOD, opt.) ─┘                                      ├─> smoke index ──────┼─> Streamlit app
                                                                   └─> forecast ─────────┘
```
1. **Fetch:** download MODIS and VIIRS detections for the study area (69.0–77.8° E, 27.5–33.0° N) via the FIRMS API.
2. **Harmonize:** unified schema; MODIS confidence `<30 low / 30–79 nominal / ≥80 high`; DBSCAN (haversine, 1 km) per day → fire events tagged `MODIS`, `VIIRS` or `both`.
3. **Calendar:** day-of-year × year matrix, season statistics, critical days (above the 90th percentile for that time of year), trend tests (Mann-Kendall, Sen's slope).
4. **Smoke Exposure Index:** fires within 300 km of Lahore that lie within ±45° of the wind direction, weighted by fire power ÷ distance, boosted on calm and humid days, scaled to 0–100.
5. **Forecast:** LightGBM on lagged fire counts, seasonality and weather; trained on 2012–2022, tested on 2024–2025 against persistence and seasonal-naive baselines.

Full technical details: [`docs/SPEC.md`](docs/SPEC.md) · Product goals: [`docs/PRD.md`](docs/PRD.md)

## Project structure
```
SmogWatch/
├── README.md
├── CLAUDE.md              # guide for AI-assisted development
├── PLAN.md                # build plan and milestones
├── requirements.txt
├── .env.example
├── docs/
│   ├── PRD.md             # product requirements
│   ├── SPEC.md            # technical specification
│   └── DATA_SOURCES.md    # datasets and sign-ups
├── src/smogwatch/
│   ├── config.py          # study area, Lahore coords, seasons, constants
│   ├── fetch_firms.py     # MODIS + VIIRS download
│   ├── harmonize.py       # unify + de-duplicate into fire events
│   ├── calendar.py        # burning activity calendar
│   ├── weather.py         # NASA POWER weather
│   ├── exposure.py        # Lahore Smoke Exposure Index
│   ├── aerosol.py         # optional AOD validation
│   ├── features.py        # model features
│   └── model.py           # 7-day forecast
├── scripts/build_all.py   # runs the full pipeline
├── app/streamlit_app.py   # web app
├── data/
│   ├── raw/               # downloaded CSVs (gitignored)
│   └── processed/         # Parquet outputs used by the app
└── tests/
```

## Getting started

### Prerequisites
- Python 3.11+
- A free **NASA FIRMS MAP_KEY**: request one at https://firms.modaps.eosdis.nasa.gov/api/map_key/
- *(Optional)* a free **NASA Earthdata login** for the aerosol layer: https://urs.earthdata.nasa.gov/

### Installation
```bash
git clone https://github.com/inamjadoon/SmogWatch.git
cd SmogWatch

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Configure secrets
```bash
cp .env.example .env
```
Then edit `.env`:
```
FIRMS_MAP_KEY=your_firms_map_key
# optional
EARTHDATA_USERNAME=
EARTHDATA_PASSWORD=
```


## Running the pipeline and app
```bash
# Download data, harmonize, build calendar, index and forecast
python scripts/build_all.py

# Launch the web app
streamlit run app/streamlit_app.py
```
Run tests:
```bash
pytest
```

## Deployment
The app is deployed on **Streamlit Community Cloud**:
1. Push the repo to GitHub.
2. At https://share.streamlit.io, create a new app → repo `inamjadoon/SmogWatch`, file `app/streamlit_app.py`.
3. Add `FIRMS_MAP_KEY` under **Settings → Secrets**.

## Roadmap
- [x] Project plan, PRD and technical spec
- [ ] Fetch MODIS + VIIRS data (2012→2026)
- [ ] Harmonization and de-duplication + tests
- [ ] Burning activity calendar
- [ ] Lahore Smoke Exposure Index
- [ ] 7-day forecast with baselines
- [ ] Streamlit app + deployment
- [ ] Aerosol (AOD) validation
- [ ] Urdu language toggle
- [ ] SMS / WhatsApp alerts for districts (post-hackathon)

## Limitations
- Satellite detections are **thermal anomalies**, not confirmed crop fires; some may be industrial or urban heat sources.
- Clouds, thick smoke and satellite overpass times mean some fires are never detected.
- The Smoke Exposure Index is a **transport proxy**, not an air-quality (PM2.5) forecast or dispersion model.
- The forecast is a hackathon prototype trained on about 14 burning seasons.

## Team
**SmogWatch**, NASA Space Apps Challenge 2026
- **Inam:** team lead, backend and AI/ML · [GitHub](https://github.com/inamjadoon) · [LinkedIn](https://www.linkedin.com/in/inamjadoon)
- _More teammates to be added_

## Acknowledgements
- Fire data: **NASA FIRMS** (Fire Information for Resource Management System), part of NASA's LANCE / EOSDIS.
- Weather data: **NASA POWER** project, NASA Langley Research Center.
- Built for the **NASA International Space Apps Challenge 2026**: *The Next Frontier*.
- AI tools (Claude) were used to assist with planning and code; all outputs are reviewed by the team.

*This project is not endorsed by NASA. NASA data is used under its open-data policy.*

## License
Released under the [MIT License](LICENSE).
