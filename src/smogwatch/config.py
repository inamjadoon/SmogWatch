"""Project-wide constants, paths and secrets.

All geographic values are WGS84 degrees. All data times are UTC; convert to PKT only in the UI.
Values follow docs/SPEC.md section 3.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT_DIR: Path = Path(__file__).resolve().parents[2]
DATA_DIR: Path = ROOT_DIR / "data"
RAW_DIR: Path = DATA_DIR / "raw"
PROCESSED_DIR: Path = DATA_DIR / "processed"

load_dotenv(ROOT_DIR / ".env")

# ---------------------------------------------------------------------------
# Study area
# ---------------------------------------------------------------------------
#: Bounding box (west, south, east, north) in degrees: Punjab (PK + IN) and Haryana.
BBOX: tuple[float, float, float, float] = (69.0, 27.5, 77.8, 33.0)

#: Lahore city centre (lat, lon) in degrees.
LAHORE: tuple[float, float] = (31.5497, 74.3436)

#: Pakistan Standard Time offset from UTC, hours (UI display only).
PKT_UTC_OFFSET_H: int = 5

# ---------------------------------------------------------------------------
# Time scope
# ---------------------------------------------------------------------------
#: Burning seasons as ((start_month, start_day), (end_month, end_day)), inclusive.
MAIN_SEASON: tuple[tuple[int, int], tuple[int, int]] = ((9, 15), (12, 15))  # rice stubble
SECOND_SEASON: tuple[tuple[int, int], tuple[int, int]] = ((4, 1), (5, 31))  # wheat stubble
SEASONS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    "main": MAIN_SEASON,
    "second": SECOND_SEASON,
}

#: Years covered (VIIRS era), inclusive.
YEARS: range = range(2012, 2027)

# ---------------------------------------------------------------------------
# Algorithm parameters
# ---------------------------------------------------------------------------
#: Cross-sensor de-duplication radius, km.
DEDUPE_EPS_KM: float = 1.0
#: Radii used for the eps sensitivity check, km.
DEDUPE_EPS_SENSITIVITY_KM: tuple[float, ...] = (0.5, 1.0, 2.0)
#: Earth radius used for haversine distances, km.
EARTH_RADIUS_KM: float = 6371.0

#: Fires further than this from Lahore are ignored by the exposure index, km.
EXPOSURE_RADIUS_KM: float = 300.0
#: A fire is "upwind" if its bearing from Lahore is within this angle of the wind-from direction, degrees.
UPWIND_HALF_ANGLE_DEG: float = 45.0
#: Minimum distance used in the exposure denominator, km (avoids blow-up for very close fires).
EXPOSURE_MIN_DIST_KM: float = 10.0

#: MODIS confidence (0-100) thresholds: < LOW_MAX -> low, >= HIGH_MIN -> high, else nominal.
MODIS_CONF_LOW_MAX: int = 30
MODIS_CONF_HIGH_MIN: int = 80

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------
FIRMS_API_BASE: str = "https://firms.modaps.eosdis.nasa.gov/api"
#: Max days per FIRMS area request (verified against live docs, Nov 2026: 1..5).
FIRMS_MAX_DAY_RANGE: int = 5
#: Pause between FIRMS calls, seconds (limit: 5000 transactions / 10 min per key).
FIRMS_SLEEP_S: float = 0.5

#: Satellite "families". Each family has a standard-processing (_SP, science archive) and a
#: near-real-time (_NRT) source. SP is used where available, NRT fills the most recent weeks.
#: VIIRS_NOAA21_SP exists per live docs (Nov 2026); SPEC.md assumed it didn't.
FIRMS_FAMILIES: dict[str, tuple[str, str]] = {
    "MODIS": ("MODIS_SP", "MODIS_NRT"),
    "VIIRS_SNPP": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"),
    "VIIRS_NOAA20": ("VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT"),
    "VIIRS_NOAA21": ("VIIRS_NOAA21_SP", "VIIRS_NOAA21_NRT"),
}
#: Satellites flying for the whole 2012+ record. Year-to-year comparisons (trends) must use only these,
#: because NOAA-20 (2018+) and NOAA-21 (2023+) add overpasses and therefore detections.
CORE_SATELLITES: tuple[str, ...] = ("Terra", "Aqua", "N")
SOURCES_SP: tuple[str, ...] = tuple(sp for sp, _ in FIRMS_FAMILIES.values())
SOURCES_NRT: tuple[str, ...] = tuple(nrt for _, nrt in FIRMS_FAMILIES.values())

POWER_API_BASE: str = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMETERS: tuple[str, ...] = ("WS10M", "WD10M", "RH2M", "T2M", "PRECTOTCORR")

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


def get_secret(name: str, default: str | None = None) -> str | None:
    """Read a secret from the environment / .env, falling back to Streamlit Secrets if available."""
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st  # imported lazily: pipeline scripts don't need Streamlit

        return st.secrets.get(name, default)  # type: ignore[no-any-return]
    except Exception:
        return default


def firms_map_key() -> str:
    """Return the FIRMS MAP_KEY or raise a clear error if it is not configured."""
    key = get_secret("FIRMS_MAP_KEY")
    if not key or key == "your_firms_map_key":
        raise RuntimeError(
            "FIRMS_MAP_KEY is not set. Copy .env.example to .env and add your key "
            "(https://firms.modaps.eosdis.nasa.gov/api/map_key/)."
        )
    return key


def season_bounds(year: int, season: str = "main") -> tuple[date, date]:
    """Return the inclusive (start, end) dates of a burning season in a given year."""
    (sm, sd), (em, ed) = SEASONS[season]
    return date(year, sm, sd), date(year, em, ed)
