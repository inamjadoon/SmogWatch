"""NASA POWER daily weather for Lahore (no API key needed).

Endpoint (checked against the live API, Nov 2026)::

    https://power.larc.nasa.gov/api/temporal/daily/point?parameters=WS10M,WD10M,RH2M,T2M,PRECTOTCORR
        &community=AG&longitude=..&latitude=..&start=YYYYMMDD&end=YYYYMMDD&format=JSON

* Missing values are -999 and are converted to NaN. The most recent ~3 days are usually still -999 (processing lag).
* WD10M is the meteorological wind direction: the direction the wind blows **from**, in degrees clockwise from north.

Output ``weather_lahore.parquet``: date, ws10m_ms, wd10m_deg, rh2m_pct, t2m_c, precip_mm.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from smogwatch import config

RENAME = {"WS10M": "ws10m_ms", "WD10M": "wd10m_deg", "RH2M": "rh2m_pct", "T2M": "t2m_c",
          "PRECTOTCORR": "precip_mm"}
FILL_VALUE = -999.0


def power_url(lat: float, lon: float, start: date, end: date) -> str:
    """Build a NASA POWER daily point request URL."""
    return (f"{config.POWER_API_BASE}?parameters={','.join(config.POWER_PARAMETERS)}&community=AG"
            f"&longitude={lon}&latitude={lat}&start={start:%Y%m%d}&end={end:%Y%m%d}&format=JSON")


def parse_power_json(payload: dict) -> pd.DataFrame:
    """Turn a POWER JSON response into a daily DataFrame. -999 becomes NaN."""
    params = payload["properties"]["parameter"]
    df = pd.DataFrame(params)
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df = df.replace(FILL_VALUE, np.nan).rename(columns=RENAME)
    df.index.name = "date"
    return df.reset_index()[["date", *RENAME.values()]]


def fetch_weather(lat: float = config.LAHORE[0], lon: float = config.LAHORE[1],
                  start: date = date(config.YEARS.start, 1, 1), end: date | None = None) -> pd.DataFrame:
    """Download daily weather for one point (default: Lahore)."""
    end = end or date.today()
    resp = requests.get(power_url(lat, lon, start, end), timeout=300)
    resp.raise_for_status()
    return parse_power_json(resp.json())


def build(out_dir: Path = config.PROCESSED_DIR) -> pd.DataFrame:
    """Fetch Lahore weather for the whole record and save weather_lahore.parquet."""
    df = fetch_weather()
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_dir / "weather_lahore.parquet", index=False)
    return df


if __name__ == "__main__":
    w = build()
    valid = w.dropna(subset=["wd10m_deg"])
    print(f"weather: {len(w)} days, valid through {valid['date'].max():%Y-%m-%d}")
