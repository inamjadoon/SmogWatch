"""Lahore Smoke Exposure Index (LSEI): how much fire activity is upwind of Lahore each day.

For each date d (docs/SPEC.md section 4.5):

1. Fire events on d within EXPOSURE_RADIUS_KM (300 km) of Lahore.
2. A fire is **upwind** if the bearing from Lahore to it is within ±UPWIND_HALF_ANGLE_DEG (45°) of the wind's
   "from" direction (NASA POWER WD10M).
3. Raw score S = Σ_upwind max_frp_mw / max(distance_km, 10). Each fire uses its max FRP, so repeat overpasses
   of the same fire are not double-counted.
4. Stagnation multiplier M = 1 + 0.5·[ws10m < 2 m/s] + 0.25·[rh2m > 70 %].
5. LSEI = 100 · clip(S·M / p99(S·M over all in-season history), 0, 1).

This is a **transport proxy**, not a dispersion model or a PM2.5 forecast. Days without weather give NaN.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from smogwatch import config, geo


def angle_diff_deg(a, b):
    """Smallest absolute difference between two compass angles, in degrees (0..180)."""
    d = np.abs((np.asarray(a) - np.asarray(b)) % 360)
    return np.minimum(d, 360 - d)


def stagnation_multiplier(ws10m_ms, rh2m_pct):
    """1 + 0.5 if wind < 2 m/s, + 0.25 if RH > 70 %. NaN weather gives no boost."""
    ws, rh = np.asarray(ws10m_ms, dtype=float), np.asarray(rh2m_pct, dtype=float)
    return 1 + 0.5 * (ws < 2.0) + 0.25 * (rh > 70.0)


def fire_geometry(events: pd.DataFrame, lahore: tuple[float, float] = config.LAHORE) -> pd.DataFrame:
    """Events within EXPOSURE_RADIUS_KM of Lahore, with distance_km and bearing_deg (from Lahore to the fire)."""
    lat0, lon0 = lahore
    dist = geo.haversine_km(lat0, lon0, events["lat"].to_numpy(), events["lon"].to_numpy())
    near = events.loc[dist <= config.EXPOSURE_RADIUS_KM].copy()
    near["distance_km"] = dist[dist <= config.EXPOSURE_RADIUS_KM]
    near["bearing_deg"] = geo.bearing_deg(lat0, lon0, near["lat"].to_numpy(), near["lon"].to_numpy())
    return near


def daily_raw_score(events: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """Per date: raw score, number of upwind fires and the Pakistan share of upwind FRP.

    ``events`` needs date, lat, lon, max_frp_mw, country. ``weather`` needs date, wd10m_deg, ws10m_ms, rh2m_pct.
    Returns date, lsei_raw, lsei_upwind_events, lsei_upwind_frp_mw, lsei_pk_share, ws10m_ms, wd10m_deg, rh2m_pct.
    """
    w = weather.copy()
    w["date"] = pd.to_datetime(w["date"])
    near = fire_geometry(events)
    near["date"] = pd.to_datetime(near["date"])
    near = near.merge(w[["date", "wd10m_deg"]], on="date", how="left")
    near["upwind"] = angle_diff_deg(near["bearing_deg"], near["wd10m_deg"]) <= config.UPWIND_HALF_ANGLE_DEG
    up = near[near["upwind"]].copy()
    up["frp"] = up["max_frp_mw"].fillna(0).clip(lower=0)
    up["contrib"] = up["frp"] / np.maximum(up["distance_km"], config.EXPOSURE_MIN_DIST_KM)
    up["frp_pk"] = up["frp"].where(up["country"] == "PK", 0.0)
    g = up.groupby("date").agg(S=("contrib", "sum"), lsei_upwind_events=("contrib", "size"),
                               lsei_upwind_frp_mw=("frp", "sum"), frp_pk=("frp_pk", "sum"))

    out = w.set_index("date")[["ws10m_ms", "wd10m_deg", "rh2m_pct"]].join(g, how="left")
    out[["S", "lsei_upwind_events", "lsei_upwind_frp_mw", "frp_pk"]] = (
        out[["S", "lsei_upwind_events", "lsei_upwind_frp_mw", "frp_pk"]].fillna(0))
    out["lsei_raw"] = out["S"] * stagnation_multiplier(out["ws10m_ms"], out["rh2m_pct"])
    no_wind = out["wd10m_deg"].isna()
    out.loc[no_wind, ["lsei_raw", "lsei_upwind_events", "lsei_upwind_frp_mw"]] = np.nan
    out["lsei_pk_share"] = np.where(out["lsei_upwind_frp_mw"] > 0,
                                    out["frp_pk"] / out["lsei_upwind_frp_mw"], np.nan)
    out = out.drop(columns=["S", "frp_pk"]).reset_index()
    return out


def scale_lsei(raw: pd.Series, reference: pd.Series) -> pd.Series:
    """Scale raw scores to 0-100 using the p99 of ``reference`` (in-season history)."""
    p99 = np.nanpercentile(reference.dropna(), 99) if reference.notna().any() else np.nan
    if not p99 or np.isnan(p99):
        return raw * np.nan
    return 100 * np.clip(raw / p99, 0, 1)


def build(processed_dir: Path = config.PROCESSED_DIR, season: str = "main") -> pd.DataFrame:
    """Compute LSEI for every in-season day with weather. Writes lsei.parquet.

    Uses all-satellite events (best coverage for the current day). The p99 reference is all in-season days.
    """
    from smogwatch import calendar as cal

    events = pd.read_parquet(processed_dir / "fire_events.parquet",
                             columns=["date", "lat", "lon", "max_frp_mw", "country"])
    weather = pd.read_parquet(processed_dir / "weather_lahore.parquet")
    years = sorted(pd.to_datetime(events["date"]).dt.year.unique())
    in_season = cal.season_dates(years, season)
    weather = weather[pd.to_datetime(weather["date"]).isin(in_season)]
    df = daily_raw_score(events, weather)
    df["lsei"] = scale_lsei(df["lsei_raw"], df["lsei_raw"])
    df.to_parquet(processed_dir / "lsei.parquet", index=False)
    return df


if __name__ == "__main__":
    d = build()
    v = d.dropna(subset=["lsei"])
    print(f"LSEI: {len(v)} days with weather, latest {v['date'].max():%Y-%m-%d} = {v['lsei'].iloc[-1]:.0f}")
    print(v.groupby(v["date"].dt.year)["lsei"].mean().round(1).to_string())
