"""Exposure geometry tests: upwind > downwind (= 0), closer > farther, stagnation boost, weather parsing."""

import numpy as np
import pandas as pd
import pytest

from smogwatch import config, exposure as ex, weather as wx

LAT0, LON0 = config.LAHORE


def ev(lat, lon, frp=10.0, date="2025-11-01", country="IN"):
    return {"date": pd.Timestamp(date), "lat": lat, "lon": lon, "max_frp_mw": frp, "country": country}


def wx_row(wd, ws=5.0, rh=40.0, date="2025-11-01"):
    return pd.DataFrame([{"date": pd.Timestamp(date), "wd10m_deg": wd, "ws10m_ms": ws, "rh2m_pct": rh}])


EAST_FIRE = ev(LAT0, LON0 + 0.5)  # ~47 km east of Lahore, bearing ~90°


def score(events, weather):
    return ex.daily_raw_score(pd.DataFrame(events), weather)["lsei_raw"].iloc[0]


def test_angle_diff_wraps():
    assert ex.angle_diff_deg(350, 10) == 20
    assert ex.angle_diff_deg(90, 270) == 180


def test_upwind_fire_scores_and_downwind_is_zero():
    upwind = score([EAST_FIRE], wx_row(wd=90))     # wind from the east carries smoke to Lahore
    downwind = score([EAST_FIRE], wx_row(wd=270))  # wind from the west carries it away
    assert upwind > 0 and downwind == 0


def test_closer_fire_scores_higher():
    near = score([ev(LAT0, LON0 + 0.3)], wx_row(wd=90))
    far = score([ev(LAT0, LON0 + 1.5)], wx_row(wd=90))
    assert near > far > 0


def test_fire_beyond_radius_ignored():
    assert score([ev(LAT0, LON0 + 4.0)], wx_row(wd=90)) == 0  # ~380 km


def test_stagnation_boost():
    calm = score([EAST_FIRE], wx_row(wd=90, ws=1.0, rh=80))
    windy = score([EAST_FIRE], wx_row(wd=90, ws=5.0, rh=40))
    assert calm == pytest.approx(windy * 1.75)


def test_missing_wind_gives_nan_and_pk_share():
    assert np.isnan(score([EAST_FIRE], wx_row(wd=np.nan)))
    d = ex.daily_raw_score(pd.DataFrame([EAST_FIRE, {**ev(LAT0, LON0 + 0.4, frp=30.0), "country": "PK"}]),
                           wx_row(wd=90))
    assert d["lsei_pk_share"].iloc[0] == pytest.approx(0.75)


def test_scale_lsei_bounds():
    ref = pd.Series(np.arange(101, dtype=float))
    s = ex.scale_lsei(pd.Series([0.0, 99.0, 500.0]), ref)
    assert s.iloc[0] == 0 and s.iloc[2] == 100 and 0 < s.iloc[1] <= 100


def test_parse_power_json_fill_values():
    payload = {"properties": {"parameter": {
        "WS10M": {"20261112": 2.0, "20261113": -999.0}, "WD10M": {"20261112": 150.0, "20261113": -999.0},
        "RH2M": {"20261112": 34.0, "20261113": -999.0}, "T2M": {"20261112": 24.5, "20261113": -999.0},
        "PRECTOTCORR": {"20261112": 0.0, "20261113": -999.0}}}}
    df = wx.parse_power_json(payload)
    assert list(df.columns) == ["date", "ws10m_ms", "wd10m_deg", "rh2m_pct", "t2m_c", "precip_mm"]
    assert df["wd10m_deg"].iloc[0] == 150.0 and df.iloc[1, 1:].isna().all()
