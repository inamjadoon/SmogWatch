# SmogWatch Punjab — Data Sources & Sign-ups

All core data is free and open from NASA. Always confirm endpoint formats against the live docs.

## 1. NASA FIRMS — fire detections (REQUIRED)
- **What:** Active-fire / thermal-anomaly detections from MODIS (Collection 6.1, 1 km) and VIIRS
  (375 m, Suomi NPP, NOAA-20, NOAA-21).
- **Site:** https://firms.modaps.eosdis.nasa.gov/
- **API docs:** https://firms.modaps.eosdis.nasa.gov/api/
- **Sign-up:** free MAP_KEY via email at https://firms.modaps.eosdis.nasa.gov/api/map_key/ ✅ (done)
- **Store as:** `FIRMS_MAP_KEY` in `.env` (local) and Streamlit Secrets (deployed). Never commit it.
- **Products:** `_SP` = standard/science-quality archive; `_NRT` = near-real-time (recent weeks).
- **Citation:** NASA FIRMS, LANCE / NASA EOSDIS. Cite MODIS C6.1 and VIIRS 375 m products in the About page.

## 2. NASA POWER — daily weather (REQUIRED, no sign-up)
- **What:** Daily wind speed/direction at 10 m, humidity, temperature, precipitation (from MERRA-2 / GEOS).
- **Docs:** https://power.larc.nasa.gov/docs/
- **Parameters:** `WS10M, WD10M, RH2M, T2M, PRECTOTCORR`
- **Sign-up:** none.

## 3. NASA Earthdata — MODIS aerosol (OPTIONAL)
- **What:** Daily aerosol optical depth (haze) over Lahore, for validating the smoke index.
- **Products:** `MOD08_D3` (Terra) / `MYD08_D3` (Aqua), or MAIAC `MCD19A2`.
- **Sign-up:** free Earthdata Login at https://urs.earthdata.nasa.gov/
- **Python:** `earthaccess` library handles login and download.

## 4. Boundaries (for Pakistan vs India split)
- Natural Earth admin-0 countries (public domain), or tag rows by FIRMS country file of origin.

## 5. Accounts & tools
| Tool | Purpose | Sign-up |
|---|---|---|
| GitHub | Code repo, submission link | Existing account (inamjadoon) |
| Streamlit Community Cloud | Free app hosting | Sign in with GitHub at https://share.streamlit.io |
| NASA Space Apps | Team + project submission page | Existing account |

## 6. Data we will show the judges
| Dataset | Agency | Used for |
|---|---|---|
| MODIS Active Fire C6.1 (Terra, Aqua) | NASA | Fire detections 2012→2026 |
| VIIRS 375 m Active Fire (SNPP, NOAA-20, NOAA-21) | NASA / NOAA | Fire detections 2012→2026 |
| NASA POWER daily | NASA | Wind, humidity, rain for smoke index + forecast |
| MODIS Aerosol (MOD08/MYD08) | NASA | Optional validation |
