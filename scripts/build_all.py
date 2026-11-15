"""One command for the whole pipeline: fetch -> harmonize -> calendar -> weather -> exposure -> app data.

    python scripts/build_all.py                 # refresh recent fires + weather, rebuild everything
    python scripts/build_all.py --skip-fetch    # rebuild from cached data only (no FIRMS calls)
    python scripts/build_all.py --history       # also (re)fetch the full 2012-now main seasons (slow first time)

Runs sanity checks (no events outside the bbox, no future dates, FRP >= 0) and writes the small files the
Streamlit app reads to data/processed/ (all committed; the big parquets are gitignored).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smogwatch import calendar as cal  # noqa: E402
from smogwatch import config, exposure, fetch_firms, harmonize, weather  # noqa: E402

log = logging.getLogger("build_all")
P = config.PROCESSED_DIR
RECENT_DAYS = 14


def sanity_checks(events: pd.DataFrame) -> None:
    """Fail loudly if the event table breaks basic physical or logical constraints."""
    w, s, e, n = config.BBOX
    d = pd.to_datetime(events["date"])
    checks = {
        "events outside bbox": (~events["lon"].between(w, e) | ~events["lat"].between(s, n)).sum(),
        "future dates": (d.dt.date > date.today() + timedelta(days=1)).sum(),
        "negative FRP": (events["max_frp_mw"] < 0).sum(),
    }
    bad = {k: int(v) for k, v in checks.items() if v}
    if bad:
        raise SystemExit(f"sanity checks failed: {bad}")
    log.info("sanity checks passed (%s events)", f"{len(events):,}")


def export_app_data() -> dict:
    """Write small app-ready files plus meta.json."""
    ev = pd.read_parquet(P / "fire_events.parquet")
    ev["date"] = pd.to_datetime(ev["date"])
    sanity_checks(ev)
    latest = ev["date"].max()
    recent = ev[ev["date"] > latest - pd.Timedelta(days=RECENT_DAYS)]
    recent.to_parquet(P / "recent_events.parquet", index=False)

    lsei = pd.read_parquet(P / "lsei.parquet")
    valid = lsei.dropna(subset=["lsei"])
    daily = pd.read_parquet(P / "daily_counts.parquet")
    d_all = daily[(daily["constellation"] == "all")]
    meta = {
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "fire_data_latest_date": f"{latest:%Y-%m-%d}",
        "weather_latest_date": f"{valid['date'].max():%Y-%m-%d}" if len(valid) else None,
        "n_events_total": int(len(ev)),
        "n_detections_total": int(d_all.loc[d_all["country"] == "ALL", "n_detections"].sum()),
        "same_date_vs_last_year": cal.same_date_comparison(d_all, latest.date()),
        "same_date_vs_last_year_core": cal.same_date_comparison(
            daily[daily["constellation"] == "core"], latest.date()),
        "core_satellites": list(config.CORE_SATELLITES),
    }
    (P / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    log.info("app data exported: %s recent events, meta %s", len(recent), meta["fire_data_latest_date"])
    return meta


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-fetch", action="store_true", help="no FIRMS / POWER calls; use cached data")
    ap.add_argument("--history", action="store_true", help="also fetch the full main-season history")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not args.skip_fetch:
        avail = fetch_firms.data_availability()
        if args.history:
            for y in config.YEARS:
                fetch_firms.fetch_season(y, "main", availability=avail)
        end = date.today()
        fetch_firms.fetch_period(end - timedelta(days=RECENT_DAYS), end, availability=avail)
        weather.build()

    harmonize.build_all()
    cal.build_all()
    exposure.build()
    meta = export_app_data()
    print(json.dumps(meta, indent=2, default=str))


if __name__ == "__main__":
    main()
