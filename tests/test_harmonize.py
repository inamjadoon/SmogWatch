"""Tests for harmonize: confidence mapping, time parsing, schema, filtering and cross-sensor de-duplication."""

import pandas as pd
import pytest
from shapely.geometry import box

from smogwatch import geo, harmonize as h

# Synthetic boundaries: everything west of 74.5 E is "PK", east is "IN" (no network needed).
BOUNDS = {"PK": box(60, 20, 74.5, 40), "IN": box(74.5, 20, 90, 40)}


def raw_row(source, lat, lon, conf, date="2025-10-20", time="0812", frp=5.0, **extra):
    modis = source.startswith("MODIS")
    row = {
        "latitude": lat, "longitude": lon, "scan": 1.0 if modis else 0.4, "track": 1.0 if modis else 0.4,
        "acq_date": date, "acq_time": time, "satellite": "Aqua" if modis else "N",
        "instrument": "MODIS" if modis else "VIIRS", "confidence": str(conf), "version": "6.1",
        "frp": frp, "daynight": "D", "source": source,
        ("brightness" if modis else "bright_ti4"): 320.0,
    }
    row.update(extra)
    return row


def dets(rows):
    return h.to_detections(pd.DataFrame(rows), boundaries=BOUNDS)


def test_modis_confidence_thresholds():
    s = pd.Series(["0", "29", "30", "79", "80", "100"])
    assert h.modis_confidence_class(s).tolist() == ["low", "low", "nominal", "nominal", "high", "high"]


def test_viirs_confidence_mapping():
    s = pd.Series(["l", "n", "h", "N", "high"])
    assert h.viirs_confidence_class(s).tolist() == ["low", "nominal", "high", "nominal", "high"]


def test_time_parsing_pads_short_hhmm():
    t = h.parse_time_utc(pd.Series(["2026-11-11", "2026-11-11"]), pd.Series(["743", "0005"]))
    assert t.dt.strftime("%H:%M").tolist() == ["07:43", "00:05"]
    assert str(t.dt.tz) == "UTC"


def test_detection_schema_and_country():
    d = dets([raw_row("MODIS_SP", 31.0, 74.0, 85), raw_row("VIIRS_NOAA20_NRT", 30.0, 75.0, "n")])
    assert list(d.columns) == h.DETECTION_COLUMNS
    assert set(d["sensor"]) == {"MODIS", "VIIRS"}
    by_sensor = d.set_index("sensor")
    assert by_sensor.loc["MODIS", "country"] == "PK" and by_sensor.loc["VIIRS", "country"] == "IN"
    assert by_sensor.loc["VIIRS", "satellite"] == "N20"  # taken from the source, not the CSV
    assert by_sensor.loc["MODIS", "pixel_km2"] == pytest.approx(1.0)


def test_outside_bbox_dropped():
    d = dets([raw_row("MODIS_SP", 35.0, 74.0, 85), raw_row("MODIS_SP", 31.0, 74.0, 85)])
    assert len(d) == 1


def test_static_sources_dropped_when_type_known():
    d = dets([raw_row("MODIS_SP", 31.0, 74.0, 85, type=2), raw_row("MODIS_SP", 31.1, 74.0, 85, type=0)])
    assert len(d) == 1 and d["fire_type"].iloc[0] == 0


def test_same_fire_both_sensors_merged():
    # ~300 m apart, same day, MODIS + VIIRS -> one event seen by both
    d = dets([raw_row("MODIS_SP", 31.000, 74.000, 85, frp=30, time="0530"),
              raw_row("VIIRS_SNPP_SP", 31.002, 74.002, "h", frp=10, time="0815")])
    ev = h.build_events(d)
    assert len(ev) == 1
    e = ev.iloc[0]
    assert e["seen_by"] == "both" and e["n_detections"] == 2
    assert e["max_frp_mw"] == 30 and e["sum_frp_mw"] == 40
    assert e["best_confidence"] == "high"
    assert e["first_time_utc"].strftime("%H%M") == "0530"
    assert 31.000 < e["lat"] < 31.001  # FRP-weighted towards the MODIS point


def test_far_apart_and_different_days_not_merged():
    d = dets([raw_row("VIIRS_SNPP_SP", 31.0, 74.0, "n"),
              raw_row("VIIRS_SNPP_SP", 31.05, 74.0, "n"),             # ~5.5 km away
              raw_row("VIIRS_SNPP_SP", 31.0, 74.0, "n", date="2025-10-21")])
    ev = h.build_events(d)
    assert len(ev) == 3 and set(ev["seen_by"]) == {"VIIRS"}


def test_low_confidence_excluded_by_default():
    d = dets([raw_row("VIIRS_SNPP_SP", 31.0, 74.0, "l"), raw_row("MODIS_SP", 30.0, 73.0, 10)])
    assert h.build_events(d).empty
    assert len(h.build_events(d, min_confidence="low")) == 2


def test_eps_sensitivity_monotonic():
    d = dets([raw_row("VIIRS_SNPP_SP", 31.0, 74.0 + 0.008 * i, "n") for i in range(5)])  # ~760 m spacing
    sens = h.eps_sensitivity(d)
    assert sens["n_events"].is_monotonic_decreasing
    assert sens.set_index("eps_km").loc[0.5, "n_events"] == 5
    assert sens.set_index("eps_km").loc[1.0, "n_events"] == 1


def test_haversine_and_bearing():
    assert geo.haversine_km(31.0, 74.0, 32.0, 74.0) == pytest.approx(111.2, abs=0.2)
    assert geo.bearing_deg(31.0, 74.0, 32.0, 74.0) == pytest.approx(0.0, abs=0.01)
    assert geo.bearing_deg(31.0, 74.0, 31.0, 73.0) == pytest.approx(270.0, abs=0.5)
