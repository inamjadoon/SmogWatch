"""Geography helpers: haversine distance, bearings, and Pakistan / India tagging.

Country boundaries: Natural Earth 1:10m Admin-0 countries (public domain), downloaded once. Only Pakistan and India
are kept, clipped to the study bbox (plus a margin), and saved to ``data/processed/boundaries_pk_in.geojson``
so the app and tests do not need the full file. Natural Earth uses de facto boundaries, so Kashmir follows the
Line of Control.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import requests
import shapely
from shapely.geometry import box, mapping, shape

from smogwatch import config

NATURAL_EARTH_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
    "ne_10m_admin_0_countries.geojson"
)
BOUNDARIES_PATH: Path = config.PROCESSED_DIR / "boundaries_pk_in.geojson"
#: ISO-3166 alpha-3 -> code used in our data.
COUNTRIES: dict[str, str] = {"PAK": "PK", "IND": "IN"}


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km. Arguments are degrees (scalars or arrays)."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * config.EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2, in degrees clockwise from north (0..360)."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    x = np.sin(lon2 - lon1) * np.cos(lat2)
    y = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(lon2 - lon1)
    return (np.degrees(np.arctan2(x, y)) + 360) % 360


def build_boundaries(path: Path = BOUNDARIES_PATH, margin_deg: float = 1.0) -> Path:
    """Download Natural Earth admin-0, keep PK + IN clipped to the bbox (plus margin), and save as GeoJSON."""
    resp = requests.get(NATURAL_EARTH_URL, timeout=300)
    resp.raise_for_status()
    world = resp.json()
    w, s, e, n = config.BBOX
    clip = box(w - margin_deg, s - margin_deg, e + margin_deg, n + margin_deg)
    feats = []
    for f in world["features"]:
        props = f["properties"]
        iso = props.get("ADM0_A3") or props.get("ISO_A3")
        if iso in COUNTRIES:
            geom = shape(f["geometry"]).intersection(clip)
            if not geom.is_empty:
                feats.append({"type": "Feature", "properties": {"country": COUNTRIES[iso]},
                              "geometry": mapping(geom)})
    if len(feats) != len(COUNTRIES):
        raise RuntimeError(f"expected {len(COUNTRIES)} countries, found {len(feats)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    return path


def load_boundaries(path: Path = BOUNDARIES_PATH) -> dict[str, shapely.Geometry]:
    """Return {country_code: geometry}. Builds the file on first use."""
    if not path.exists():
        build_boundaries(path)
    fc = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for f in fc["features"]:
        geom = shape(f["geometry"])
        shapely.prepare(geom)
        out[f["properties"]["country"]] = geom
    return out


def assign_country(lat, lon, boundaries: dict[str, shapely.Geometry] | None = None) -> np.ndarray:
    """Tag points with 'PK' / 'IN', or 'other' if they fall in neither country. lat/lon in degrees."""
    boundaries = boundaries if boundaries is not None else load_boundaries()
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    out = np.full(lat.shape, "other", dtype=object)
    for code, geom in boundaries.items():
        inside = shapely.contains_xy(geom, lon, lat)
        out[inside & (out == "other")] = code
    return out
