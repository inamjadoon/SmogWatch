"""Download MODIS + VIIRS active-fire detections from the NASA FIRMS Area API.

Endpoint (verified against https://firms.modaps.eosdis.nasa.gov/api/area/, Nov 2026)::

    /api/area/csv/{MAP_KEY}/{SOURCE}/{W,S,E,N}/{DAY_RANGE}/{YYYY-MM-DD}

This returns detections for DATE .. DATE + DAY_RANGE - 1 (forward), with DAY_RANGE in 1..5.

Every request is cached at ``data/raw/{SOURCE}/{start}_{range}.csv`` and skipped if already present.
Chunks that touch the last few days are always re-fetched, because NRT data for those days is still arriving.

CLI examples::

    python -m smogwatch.fetch_firms --years 2025 --season main
    python -m smogwatch.fetch_firms --years 2012-2026 --season main second
    python -m smogwatch.fetch_firms --recent 10        # last 10 days, NRT
"""

from __future__ import annotations

import argparse
import io
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd
import requests

from smogwatch import config

log = logging.getLogger(__name__)

#: Chunks whose last day is within this many days of today are never treated as final.
FRESH_DAYS: int = 3
#: HTTP statuses that trigger a retry with exponential backoff.
RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES: int = 5


class FirmsError(RuntimeError):
    """FIRMS returned an error message instead of CSV data."""


# ---------------------------------------------------------------------------
# URL / parsing helpers (pure, unit-tested)
# ---------------------------------------------------------------------------


def bbox_str(bbox: tuple[float, float, float, float] = config.BBOX) -> str:
    """Format a (W, S, E, N) bbox as FIRMS expects: 'W,S,E,N'."""
    return ",".join(f"{v:g}" for v in bbox)


def area_url(
    map_key: str,
    source: str,
    start: date,
    day_range: int,
    bbox: tuple[float, float, float, float] = config.BBOX,
) -> str:
    """Build a FIRMS Area API CSV URL for ``day_range`` days starting at ``start``."""
    if not 1 <= day_range <= config.FIRMS_MAX_DAY_RANGE:
        raise ValueError(f"day_range must be 1..{config.FIRMS_MAX_DAY_RANGE}, got {day_range}")
    return (
        f"{config.FIRMS_API_BASE}/area/csv/{map_key}/{source}/"
        f"{bbox_str(bbox)}/{day_range}/{start.isoformat()}"
    )


def parse_firms_csv(text: str) -> pd.DataFrame:
    """Parse a FIRMS CSV response. Raises FirmsError if the body is an error message instead of CSV.

    A header-only response (no fires) gives an empty DataFrame with the header columns.
    """
    body = text.strip()
    if not body:
        return pd.DataFrame()
    first_line = body.splitlines()[0].lower()
    if "latitude" not in first_line:
        # FIRMS answers errors with plain text, e.g. "Invalid MAP_KEY." or a transaction-limit notice.
        raise FirmsError(body[:300])
    return pd.read_csv(io.StringIO(body), dtype={"acq_time": str, "confidence": str, "version": str})


def date_chunks(start: date, end: date, size: int = config.FIRMS_MAX_DAY_RANGE) -> Iterator[tuple[date, int]]:
    """Split the inclusive range [start, end] into (chunk_start, day_range) pieces of at most ``size`` days."""
    cur = start
    while cur <= end:
        n = min(size, (end - cur).days + 1)
        yield cur, n
        cur += timedelta(days=n)


def cache_path(source: str, start: date, day_range: int, raw_dir: Path = config.RAW_DIR) -> Path:
    """Return the cache location of one request."""
    return raw_dir / source / f"{start.isoformat()}_{day_range}.csv"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _get(session: requests.Session, url: str) -> str:
    """GET with retry and exponential backoff on 429/5xx and connection errors."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=120)
        except requests.RequestException as exc:
            wait = 2**attempt * 5
            log.warning("request failed (%s), retrying in %ss", exc, wait)
            time.sleep(wait)
            continue
        if resp.status_code in RETRY_STATUSES:
            wait = 2**attempt * 5
            log.warning("HTTP %s, retrying in %ss", resp.status_code, wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        if "exceeding" in resp.text[:200].lower():  # transaction limit reached: wait it out
            log.warning("FIRMS transaction limit reached; sleeping 60 s")
            time.sleep(60)
            continue
        return resp.text
    raise FirmsError(f"giving up after {MAX_RETRIES} attempts: {url.split('/csv/')[0]}/csv/***")


def fetch_area(
    source: str,
    start: date,
    day_range: int,
    bbox: tuple[float, float, float, float] = config.BBOX,
    *,
    session: requests.Session | None = None,
    map_key: str | None = None,
    raw_dir: Path = config.RAW_DIR,
    today: date | None = None,
) -> pd.DataFrame:
    """Fetch one FIRMS request (at most 5 days), using the on-disk cache when possible.

    Returns raw FIRMS columns plus ``source`` (the FIRMS source name).
    """
    today = today or date.today()
    path = cache_path(source, start, day_range, raw_dir)
    chunk_end = start + timedelta(days=day_range - 1)
    is_fresh = chunk_end >= today - timedelta(days=FRESH_DAYS)

    if path.exists() and not is_fresh:
        text = path.read_text(encoding="utf-8")
    else:
        session = session or requests.Session()
        text = _get(session, area_url(map_key or config.firms_map_key(), source, start, day_range, bbox))
        df_check = parse_firms_csv(text)  # validate before caching, so error messages are never cached
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        time.sleep(config.FIRMS_SLEEP_S)
        del df_check

    df = parse_firms_csv(text)
    df["source"] = source
    return df


def fetch_range(
    source: str,
    start: date,
    end: date,
    bbox: tuple[float, float, float, float] = config.BBOX,
    **kwargs,
) -> pd.DataFrame:
    """Fetch all detections for one source over the inclusive range [start, end]."""
    frames = [fetch_area(source, s, n, bbox, **kwargs) for s, n in date_chunks(start, end)]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------------------
# SP vs NRT selection
# ---------------------------------------------------------------------------


def data_availability(
    map_key: str | None = None, session: requests.Session | None = None
) -> pd.DataFrame:
    """Return the FIRMS data-availability table with columns [data_id, min_date, max_date] (dates)."""
    session = session or requests.Session()
    url = f"{config.FIRMS_API_BASE}/data_availability/csv/{map_key or config.firms_map_key()}/ALL"
    df = pd.read_csv(io.StringIO(_get(session, url)))
    df.columns = [c.strip().lower() for c in df.columns]
    for col in ("min_date", "max_date"):
        df[col] = pd.to_datetime(df[col]).dt.date
    return df


def plan_requests(
    start: date, end: date, availability: pd.DataFrame, families: Iterable[str] | None = None
) -> list[tuple[str, date, date]]:
    """Decide which FIRMS source to use per satellite family over [start, end].

    For each family: use the SP archive up to its max_date, then the NRT source for the remaining days.
    Days outside a source's available window are skipped (e.g. NOAA-21 before 2023).
    Returns a list of (source, sub_start, sub_end).
    """
    avail = availability.set_index("data_id")
    plan: list[tuple[str, date, date]] = []
    for fam in families or config.FIRMS_FAMILIES:
        sp, nrt = config.FIRMS_FAMILIES[fam]
        cursor = start
        for src in (sp, nrt):
            if src not in avail.index or cursor > end:
                continue
            lo = max(cursor, avail.at[src, "min_date"])
            hi = min(end, avail.at[src, "max_date"])
            if lo <= hi:
                plan.append((src, lo, hi))
                cursor = hi + timedelta(days=1)
    return plan


def fetch_period(
    start: date,
    end: date,
    availability: pd.DataFrame | None = None,
    families: Iterable[str] | None = None,
    **kwargs,
) -> pd.DataFrame:
    """Fetch every satellite family for [start, end], choosing SP/NRT automatically."""
    availability = availability if availability is not None else data_availability()
    frames = []
    for src, lo, hi in plan_requests(start, end, availability, families):
        log.info("fetching %s %s..%s", src, lo, hi)
        df = fetch_range(src, lo, hi, **kwargs)
        log.info("  %s rows", len(df))
        if not df.empty:
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fetch_season(year: int, season: str = "main", **kwargs) -> pd.DataFrame:
    """Fetch all satellite families for one burning season (see config.SEASONS)."""
    start, end = config.season_bounds(year, season)
    end = min(end, date.today())
    if start > end:
        return pd.DataFrame()
    return fetch_period(start, end, **kwargs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_years(spec: str) -> list[int]:
    """'2012-2026' (ascending), '2026-2012' (descending), or '2019,2020'."""
    if "-" in spec:
        a, b = (int(x) for x in spec.split("-"))
        step = 1 if b >= a else -1
        return list(range(a, b + step, step))
    return [int(y) for y in spec.split(",")]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Download FIRMS MODIS + VIIRS detections to data/raw/")
    parser.add_argument("--years", default=str(date.today().year), help="e.g. 2025, 2019,2020 or 2012-2026")
    parser.add_argument("--season", nargs="+", default=["main"], choices=list(config.SEASONS))
    parser.add_argument("--recent", type=int, help="instead of seasons, fetch the last N days")
    parser.add_argument("--families", nargs="+", choices=list(config.FIRMS_FAMILIES),
                        help="limit to these satellite families (default: all); lets you run families in parallel")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    avail = data_availability()
    if args.recent:
        end = date.today()
        df = fetch_period(end - timedelta(days=args.recent - 1), end, availability=avail,
                          families=args.families)
        print(f"recent {args.recent} days: {len(df)} detections")
        return
    for year in _parse_years(args.years):
        for season in args.season:
            df = fetch_season(year, season, availability=avail, families=args.families)
            counts = df["source"].value_counts().to_dict() if not df.empty else {}
            print(f"{year} {season}: {len(df)} detections {counts}", flush=True)


if __name__ == "__main__":
    main()
