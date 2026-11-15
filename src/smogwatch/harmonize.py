"""Harmonize raw FIRMS MODIS + VIIRS detections into one schema, then merge them into fire events.

Pipeline::

    data/raw/{SOURCE}/*.csv  --load_raw-->  raw rows
                             --to_detections-->  detections.parquet   (one row per satellite pixel)
                             --build_events-->   fire_events.parquet  (one row per fire, per UTC day)

Rules (docs/SPEC.md section 4.2):
* Confidence: VIIRS l/n/h -> low/nominal/high. MODIS 0-100: <30 low, 30-79 nominal, >=80 high.
* Keep only points inside config.BBOX. By default, events use confidence >= nominal.
* FIRMS SP archives include a ``type`` column (0 vegetation fire, 1 volcano, 2 other static land source,
  3 offshore). When it is present, only type 0 is kept, which drops known industrial / static heat sources.
  NRT data has no ``type``, so those rows are kept.
* De-duplication: for each UTC date, DBSCAN (haversine, eps = DEDUPE_EPS_KM, min_samples = 1). Every cluster is one event.

CLI::

    python -m smogwatch.harmonize            # rebuild detections + events from everything in data/raw
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN

from smogwatch import config, geo

log = logging.getLogger(__name__)

CONF_ORDER: dict[str, int] = {"low": 0, "nominal": 1, "high": 2}
#: FIRMS VIIRS satellite codes are not always consistent between SP and NRT, so the satellite comes from the source name.
SOURCE_SATELLITE: dict[str, str] = {
    "VIIRS_SNPP": "N",
    "VIIRS_NOAA20": "N20",
    "VIIRS_NOAA21": "N21",
}
MODIS_SATELLITE: dict[str, str] = {"T": "Terra", "A": "Aqua", "TERRA": "Terra", "AQUA": "Aqua"}

DETECTION_COLUMNS: list[str] = [
    "det_id", "event_time_utc", "date", "lat", "lon", "sensor", "satellite", "source",
    "confidence_raw", "confidence_class", "frp_mw", "brightness_k", "pixel_km2", "daynight",
    "fire_type", "country",
]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_raw(raw_dir: Path = config.RAW_DIR) -> pd.DataFrame:
    """Concatenate every cached FIRMS CSV under ``raw_dir/{SOURCE}/``, adding a ``source`` column.

    Overlapping chunks (e.g. a re-fetched recent window) produce duplicate rows. Exact duplicates are dropped.
    """
    frames = []
    for src_dir in sorted(p for p in raw_dir.iterdir() if p.is_dir()):
        for f in sorted(src_dir.glob("*.csv")):
            df = pd.read_csv(f, dtype={"acq_time": str, "confidence": str, "version": str})
            if not df.empty:
                df["source"] = src_dir.name
                frames.append(df)
    if not frames:
        return pd.DataFrame()
    raw = pd.concat(frames, ignore_index=True)
    key = ["source", "latitude", "longitude", "acq_date", "acq_time"]
    return raw.drop_duplicates(subset=key).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Detections
# ---------------------------------------------------------------------------


def modis_confidence_class(conf: pd.Series) -> pd.Series:
    """Map MODIS 0-100 confidence to low / nominal / high."""
    c = pd.to_numeric(conf, errors="coerce")
    out = pd.Series("nominal", index=conf.index, dtype=object)
    out[c < config.MODIS_CONF_LOW_MAX] = "low"
    out[c >= config.MODIS_CONF_HIGH_MIN] = "high"
    out[c.isna()] = pd.NA
    return out


def viirs_confidence_class(conf: pd.Series) -> pd.Series:
    """Map VIIRS l / n / h (any case, or full words) to low / nominal / high."""
    first = conf.astype(str).str.strip().str.lower().str[0]
    return first.map({"l": "low", "n": "nominal", "h": "high"}).astype(object)


def parse_time_utc(acq_date: pd.Series, acq_time: pd.Series) -> pd.Series:
    """Combine FIRMS acq_date (YYYY-MM-DD) and acq_time (HHMM, sometimes not zero-padded, e.g. '743') into UTC datetimes."""
    hhmm = acq_time.astype(str).str.split(".").str[0].str.zfill(4)
    return pd.to_datetime(acq_date.astype(str) + " " + hhmm, format="%Y-%m-%d %H%M", utc=True)


def _family(source: str) -> str:
    return source.rsplit("_", 1)[0]  # 'VIIRS_NOAA20_SP' -> 'VIIRS_NOAA20'


def to_detections(
    raw: pd.DataFrame,
    bbox: tuple[float, float, float, float] = config.BBOX,
    boundaries: dict | None = None,
) -> pd.DataFrame:
    """Convert raw FIRMS rows (MODIS and/or VIIRS, with a ``source`` column) to the unified detection schema."""
    if raw.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)
    df = raw.copy()
    w, s, e, n = bbox
    df = df[df["longitude"].between(w, e) & df["latitude"].between(s, n)]

    is_modis = df["source"].str.startswith("MODIS")
    out = pd.DataFrame(index=df.index)
    out["event_time_utc"] = parse_time_utc(df["acq_date"], df["acq_time"])
    out["date"] = out["event_time_utc"].dt.date
    out["lat"] = df["latitude"].astype(float)
    out["lon"] = df["longitude"].astype(float)
    out["sensor"] = np.where(is_modis, "MODIS", "VIIRS")
    sat_modis = df["satellite"].astype(str).str.upper().map(MODIS_SATELLITE)
    sat_viirs = df["source"].map(_family).map(SOURCE_SATELLITE)
    out["satellite"] = sat_modis.where(is_modis, sat_viirs)
    out["source"] = df["source"]
    out["confidence_raw"] = df["confidence"].astype(str)
    out["confidence_class"] = modis_confidence_class(df["confidence"]).where(
        is_modis, viirs_confidence_class(df["confidence"])
    )
    out["frp_mw"] = pd.to_numeric(df["frp"], errors="coerce")
    bright_modis = df["brightness"] if "brightness" in df else np.nan
    bright_viirs = df["bright_ti4"] if "bright_ti4" in df else np.nan
    out["brightness_k"] = pd.to_numeric(
        pd.Series(np.where(is_modis, bright_modis, bright_viirs), index=df.index), errors="coerce"
    )
    out["pixel_km2"] = df["scan"].astype(float) * df["track"].astype(float)
    out["daynight"] = df["daynight"]
    out["fire_type"] = pd.to_numeric(df["type"], errors="coerce").astype("Int8") if "type" in df else pd.NA
    out["fire_type"] = out["fire_type"].astype("Int8")

    # Drop known non-vegetation static sources (only identifiable in SP data).
    out = out[out["fire_type"].isna() | (out["fire_type"] == 0)]

    out["country"] = geo.assign_country(out["lat"].to_numpy(), out["lon"].to_numpy(), boundaries)
    key = out[["sensor", "satellite", "lat", "lon", "event_time_utc"]].astype(str)
    out["det_id"] = pd.util.hash_pandas_object(key, index=False).map("{:016x}".format)
    out = out.drop_duplicates("det_id")
    return out[DETECTION_COLUMNS].sort_values("event_time_utc").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def cluster_day(lat: np.ndarray, lon: np.ndarray, eps_km: float = config.DEDUPE_EPS_KM) -> np.ndarray:
    """DBSCAN cluster labels for one day's detections (haversine, min_samples = 1, so every point gets a label)."""
    if len(lat) == 0:
        return np.array([], dtype=int)
    coords = np.radians(np.column_stack([lat, lon]))
    return DBSCAN(eps=eps_km / config.EARTH_RADIUS_KM, min_samples=1, metric="haversine",
                  algorithm="ball_tree").fit_predict(coords)


def build_events(
    detections: pd.DataFrame,
    eps_km: float = config.DEDUPE_EPS_KM,
    min_confidence: str = "nominal",
) -> pd.DataFrame:
    """Merge detections of the same fire (same UTC day, within ``eps_km``) into fire events.

    Output columns: event_id, date, lat, lon (FRP-weighted centroid), first_time_utc, n_detections,
    n_modis, n_viirs, max_frp_mw, sum_frp_mw, best_confidence, seen_by (MODIS | VIIRS | both),
    satellites (comma-separated), country.
    """
    det = detections[
        detections["confidence_class"].map(CONF_ORDER).fillna(-1) >= CONF_ORDER[min_confidence]
    ].copy()
    if det.empty:
        return pd.DataFrame()

    labels = np.empty(len(det), dtype=np.int64)
    offset = 0
    pos = np.arange(len(det))
    for _, idx in det.groupby("date", sort=True).indices.items():
        lab = cluster_day(det["lat"].to_numpy()[idx], det["lon"].to_numpy()[idx], eps_km)
        labels[pos[idx]] = lab + offset
        offset += lab.max() + 1
    det["cluster"] = labels

    frp = det["frp_mw"].fillna(0).clip(lower=0)
    det["_w"] = np.where(frp > 0, frp, 1e-6)  # avoids zero-weight clusters
    det["_wlat"] = det["lat"] * det["_w"]
    det["_wlon"] = det["lon"] * det["_w"]
    det["_conf"] = det["confidence_class"].map(CONF_ORDER)
    det["_is_modis"] = det["sensor"] == "MODIS"

    g = det.groupby("cluster", sort=False)
    ev = pd.DataFrame({
        "date": g["date"].first(),
        "lat": g["_wlat"].sum() / g["_w"].sum(),
        "lon": g["_wlon"].sum() / g["_w"].sum(),
        "first_time_utc": g["event_time_utc"].min(),
        "n_detections": g.size(),
        "n_modis": g["_is_modis"].sum().astype(int),
        "max_frp_mw": g["frp_mw"].max(),
        "sum_frp_mw": g["frp_mw"].sum(),
        "best_confidence": g["_conf"].max().map({v: k for k, v in CONF_ORDER.items()}),
        "satellites": g["satellite"].agg(lambda s: ",".join(sorted(set(s.dropna())))),
        "country": g["country"].agg(lambda s: s.mode().iat[0]),
    })
    ev["n_viirs"] = ev["n_detections"] - ev["n_modis"]
    ev["seen_by"] = np.select(
        [(ev["n_modis"] > 0) & (ev["n_viirs"] > 0), ev["n_modis"] > 0], ["both", "MODIS"], "VIIRS"
    )
    ev = ev.sort_values(["date", "first_time_utc"]).reset_index(drop=True)
    ev.insert(0, "event_id", [f"{d}_{i:05d}" for d, i in zip(ev["date"], ev.groupby("date").cumcount())])
    cols = ["event_id", "date", "lat", "lon", "first_time_utc", "n_detections", "n_modis", "n_viirs",
            "max_frp_mw", "sum_frp_mw", "best_confidence", "seen_by", "satellites", "country"]
    return ev[cols]


def eps_sensitivity(detections: pd.DataFrame, eps_values=config.DEDUPE_EPS_SENSITIVITY_KM) -> pd.DataFrame:
    """Total event counts and seen_by split for several de-duplication radii (km)."""
    rows = []
    for eps in eps_values:
        ev = build_events(detections, eps_km=eps)
        counts = ev["seen_by"].value_counts()
        rows.append({"eps_km": eps, "n_events": len(ev), **{f"n_{k}": int(counts.get(k, 0))
                                                             for k in ("MODIS", "VIIRS", "both")}})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_all(raw_dir: Path = config.RAW_DIR, out_dir: Path = config.PROCESSED_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rebuild detections.parquet and fire_events.parquet from every cached raw file."""
    raw = load_raw(raw_dir)
    det = to_detections(raw)
    ev = build_events(det)
    out_dir.mkdir(parents=True, exist_ok=True)
    det.to_parquet(out_dir / "detections.parquet", index=False)
    ev.to_parquet(out_dir / "fire_events.parquet", index=False)
    log.info("raw %s -> detections %s -> events %s", len(raw), len(det), len(ev))
    return det, ev


def main(argv: list[str] | None = None) -> None:
    argparse.ArgumentParser(description="Harmonize data/raw into detections + fire events").parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    det, ev = build_all()
    print(f"detections: {len(det):,}  (by sensor: {det['sensor'].value_counts().to_dict()})")
    print(f"  confidence: {det['confidence_class'].value_counts().to_dict()}")
    print(f"  country:    {det['country'].value_counts().to_dict()}")
    print(f"fire events (conf >= nominal, eps {config.DEDUPE_EPS_KM} km): {len(ev):,}")
    print(f"  seen_by:    {ev['seen_by'].value_counts().to_dict()}")
    print(f"  country:    {ev['country'].value_counts().to_dict()}")
    print("eps sensitivity:")
    print(eps_sensitivity(det).to_string(index=False))


if __name__ == "__main__":
    main()
