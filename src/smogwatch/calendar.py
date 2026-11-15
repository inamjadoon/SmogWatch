"""Burning activity calendar: daily counts, year x day-of-year matrix, season stats, critical days, trends.

Note: this module shadows the stdlib ``calendar``. Always import it as ``from smogwatch import calendar``.

Zero-filling: a day with no fire events counts as 0 **only if it lies inside a downloaded season window**
(``season_dates``). Days we never fetched are absent, not 0, so missing data is never treated as "no fires".

Day-of-year is leap-aligned (``aligned_doy``): from 1 March onward, leap years are shifted by -1 so that the
same calendar date always gets the same column (e.g. 1 Nov = 305 in every year).

CLI::

    python -m smogwatch.calendar      # reads fire_events.parquet, writes daily_counts / season_stats / trends
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from smogwatch import config

log = logging.getLogger(__name__)

COUNTRIES_ALL: tuple[str, ...] = ("ALL", "PK", "IN")


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def aligned_doy(d: pd.Series) -> pd.Series:
    """Leap-aligned day of year (1..365; 29 Feb shares 59 with 28 Feb)."""
    d = pd.to_datetime(d)
    doy = d.dt.day_of_year
    shift = d.dt.is_leap_year & (doy >= 60)  # 29 Feb (60) and later move back one day
    return (doy - shift.astype(int)).astype(int)


def season_dates(years=config.YEARS, season: str = "main", today: date | None = None) -> pd.DatetimeIndex:
    """All dates inside the given season for each year, truncated at ``today`` (no future dates)."""
    today = today or date.today()
    out = []
    for y in years:
        start, end = config.season_bounds(y, season)
        end = min(end, today)
        if start <= end:
            out.append(pd.date_range(start, end, freq="D"))
    return out[0].append(out[1:]) if out else pd.DatetimeIndex([])


# ---------------------------------------------------------------------------
# Daily counts
# ---------------------------------------------------------------------------


def daily_counts(events: pd.DataFrame, dates: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """Daily fire-event counts for ALL / PK / IN, zero-filled over ``dates``.

    Columns: date, country, n_events, sum_frp_mw, n_detections, n_modis_only, n_viirs_only, n_both.
    ``n_detections`` is the raw (pre-merge) detection count, for raw-vs-merged comparisons.
    If ``dates`` is None, the range spans the event dates (no gaps filled beyond them).
    """
    ev = events.copy()
    ev["date"] = pd.to_datetime(ev["date"])
    for k, label in (("MODIS", "n_modis_only"), ("VIIRS", "n_viirs_only"), ("both", "n_both")):
        ev[label] = (ev["seen_by"] == k).astype(int)
    agg = {"n_events": ("event_id", "size"), "sum_frp_mw": ("sum_frp_mw", "sum"),
           "n_detections": ("n_detections", "sum"), "n_modis_only": ("n_modis_only", "sum"),
           "n_viirs_only": ("n_viirs_only", "sum"), "n_both": ("n_both", "sum")}

    if dates is None:
        dates = pd.DatetimeIndex(sorted(ev["date"].unique())) if not ev.empty else pd.DatetimeIndex([])
    dates = pd.DatetimeIndex(dates).normalize().unique().sort_values()
    ev = ev[ev["date"].isin(dates)]

    frames = []
    for c in COUNTRIES_ALL:
        sub = ev if c == "ALL" else ev[ev["country"] == c]
        d = sub.groupby("date").agg(**agg).reindex(dates, fill_value=0)
        d.index.name = "date"
        d = d.reset_index()
        d.insert(1, "country", c)
        frames.append(d)
    out = pd.concat(frames, ignore_index=True)
    int_cols = [c for c in agg if c != "sum_frp_mw"]
    out[int_cols] = out[int_cols].astype(int)
    out["year"] = out["date"].dt.year
    out["doy"] = aligned_doy(out["date"])
    return out


def calendar_matrix(daily: pd.DataFrame, value: str = "n_events", country: str = "ALL") -> pd.DataFrame:
    """Pivot to year (rows) x leap-aligned day-of-year (columns). Days never fetched stay NaN."""
    sub = daily[daily["country"] == country]
    return sub.pivot_table(index="year", columns="doy", values=value, aggfunc="sum")


# ---------------------------------------------------------------------------
# Season statistics
# ---------------------------------------------------------------------------


def _runs_above(x: np.ndarray, thr: float, min_len: int = 3) -> list[tuple[int, int]]:
    """(start, end) index pairs of runs where x > thr for at least min_len consecutive days."""
    runs, start = [], None
    for i, above in enumerate(np.append(x > thr, False)):
        if above and start is None:
            start = i
        elif not above and start is not None:
            if i - start >= min_len:
                runs.append((start, i - 1))
            start = None
    return runs


def activity_threshold(daily: pd.DataFrame, country: str = "ALL") -> float:
    """Season-activity threshold: p25 of pooled in-season daily counts across all years (at least 1 event)."""
    vals = daily.loc[daily["country"] == country, "n_events"]
    return float(max(np.percentile(vals, 25), 1.0)) if len(vals) else 1.0


def season_stats(daily: pd.DataFrame, country: str = "ALL", season: str = "main",
                 threshold: float | None = None) -> pd.DataFrame:
    """Per-year season statistics.

    * start / end: first day of the first, and last day of the last, run of >= 3 consecutive days with
      events > ``threshold`` (default ``activity_threshold``: pooled p25, min 1).
    * peak_date / peak_count: day with most events.
    * p10_date / p50_date / p90_date: dates when cumulative events reach 10 / 50 / 90 % of the season total.
    * total_events, total_frp_mw, days_covered, complete (season fully in the past and fully downloaded).
    """
    thr = activity_threshold(daily, country) if threshold is None else threshold
    sub = daily[daily["country"] == country].sort_values("date")
    rows = []
    for year, g in sub.groupby("year"):
        s_start, s_end = config.season_bounds(int(year), season)
        g = g[(g["date"].dt.date >= s_start) & (g["date"].dt.date <= s_end)]
        if g.empty:
            continue
        x = g["n_events"].to_numpy()
        dates = g["date"].dt.date.to_numpy()
        runs = _runs_above(x, thr)
        total = int(x.sum())
        cum = np.cumsum(x) / total if total else np.zeros_like(x, dtype=float)

        def pct_date(p: float):
            return dates[int(np.argmax(cum >= p))] if total else None

        expected_days = (s_end - s_start).days + 1
        rows.append({
            "year": int(year),
            "country": country,
            "start": dates[runs[0][0]] if runs else None,
            "end": dates[runs[-1][1]] if runs else None,
            "peak_date": dates[int(np.argmax(x))] if total else None,
            "peak_count": int(x.max()),
            "p10_date": pct_date(0.10),
            "p50_date": pct_date(0.50),
            "p90_date": pct_date(0.90),
            "total_events": total,
            "total_frp_mw": float(g["sum_frp_mw"].sum()),
            "days_covered": len(g),
            "complete": len(g) == expected_days,
            "threshold": thr,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Critical days
# ---------------------------------------------------------------------------


def critical_days(daily: pd.DataFrame, window: int = 7, pct: float = 90, min_years: int = 3) -> pd.DataFrame:
    """Flag days whose count exceeds the ``pct``-th percentile of the same time of year (±``window`` days)
    in **prior** years only (no look-ahead). Needs at least ``min_years`` prior years, otherwise not flagged.

    Adds columns ``clim_p90`` (the threshold) and ``critical`` (bool) to a copy of ``daily``.
    """
    out = []
    for country, g in daily.groupby("country", sort=False):
        g = g.copy()
        thr = np.full(len(g), np.nan)
        years = g["year"].to_numpy()
        doys = g["doy"].to_numpy()
        vals = g["n_events"].to_numpy()
        for i, (y, d) in enumerate(zip(years, doys)):
            mask = (years < y) & (np.abs(doys - d) <= window)
            if len(np.unique(years[mask])) >= min_years:
                thr[i] = np.percentile(vals[mask], pct)
        g["clim_p90"] = thr
        g["critical"] = ~np.isnan(thr) & (vals > np.nan_to_num(thr, nan=np.inf))
        out.append(g)
    return pd.concat(out).sort_index()


# ---------------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------------


def trends(stats: pd.DataFrame, complete_only: bool = True) -> dict:
    """Mann-Kendall test + Sen's slope on season totals and timing (peak / 50 % day-of-year).

    Slopes are per year (events/year, days/year). Needs at least 4 seasons, otherwise returns {}.
    """
    import pymannkendall as mk

    s = stats[stats["complete"]] if complete_only else stats
    s = s.sort_values("year")
    if len(s) < 4:
        return {}
    series = {
        "total_events": s["total_events"].astype(float),
        "total_frp_mw": s["total_frp_mw"].astype(float),
        "peak_doy": aligned_doy(pd.to_datetime(s["peak_date"])).astype(float),
        "p50_doy": aligned_doy(pd.to_datetime(s["p50_date"])).astype(float),
    }
    out = {"years": [int(s["year"].min()), int(s["year"].max())], "n_seasons": len(s)}
    for name, x in series.items():
        r = mk.original_test(x.to_numpy())
        out[name] = {"trend": r.trend, "p_value": float(r.p), "sen_slope_per_year": float(r.slope),
                     "tau": float(r.Tau)}
    return out


def same_date_comparison(daily: pd.DataFrame, on: date, country: str = "ALL",
                         season: str = "main") -> dict:
    """Season-to-date event total on ``on`` vs the same date in the previous year (for the Today KPI)."""
    sub = daily[daily["country"] == country]
    res = {}
    for label, y in (("this_year", on.year), ("last_year", on.year - 1)):
        s_start, _ = config.season_bounds(y, season)
        try:
            same = date(y, on.month, on.day)
        except ValueError:  # 29 Feb
            same = date(y, on.month, 28)
        m = (sub["date"].dt.date >= s_start) & (sub["date"].dt.date <= same)
        res[label] = int(sub.loc[m, "n_events"].sum())
    res["change_pct"] = (100 * (res["this_year"] - res["last_year"]) / res["last_year"]
                         if res["last_year"] else None)
    return res


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_all(processed_dir: Path = config.PROCESSED_DIR, season: str = "main",
              today: date | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Build the calendar for both event series and write:

    * daily_counts.parquet / season_stats.parquet: column ``constellation`` = 'all' (every satellite,
      for activity and maps) or 'core' (CORE_SATELLITES, constant over 2012+, for comparing years)
    * trends.json: computed on **core** only, so added satellites (NOAA-20 2018+, NOAA-21 2023+) do not
      create an artificial upward trend

    Only seasons with downloaded data are zero-filled: a year is included if it has at least one event.
    """
    dailies, stats_all = [], []
    for constellation, fname in (("all", "fire_events.parquet"), ("core", "fire_events_core.parquet")):
        events = pd.read_parquet(processed_dir / fname)
        ev_years = sorted(pd.to_datetime(events["date"]).dt.year.unique())
        dates = season_dates(ev_years, season, today)
        daily = critical_days(daily_counts(events, dates))
        stats = pd.concat([season_stats(daily, c, season) for c in COUNTRIES_ALL], ignore_index=True)
        daily.insert(2, "constellation", constellation)
        stats.insert(2, "constellation", constellation)
        dailies.append(daily)
        stats_all.append(stats)
    daily = pd.concat(dailies, ignore_index=True)
    stats = pd.concat(stats_all, ignore_index=True)
    core = stats[stats["constellation"] == "core"]
    tr = {"constellation": "core", "satellites": list(config.CORE_SATELLITES),
          **{c: trends(core[core["country"] == c]) for c in COUNTRIES_ALL}}

    daily.to_parquet(processed_dir / "daily_counts.parquet", index=False)
    stats.to_parquet(processed_dir / "season_stats.parquet", index=False)
    (processed_dir / "trends.json").write_text(json.dumps(tr, indent=2, default=str), encoding="utf-8")
    return daily, stats, tr


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Build the burning activity calendar from fire_events.parquet")
    p.add_argument("--season", default="main", choices=list(config.SEASONS))
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    daily, stats, tr = build_all(season=args.season)
    cols = ["year", "start", "end", "peak_date", "peak_count", "p50_date", "total_events", "complete"]
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        for cons in ("all", "core"):
            s = stats[(stats["country"] == "ALL") & (stats["constellation"] == cons)]
            print(f"\n== season stats, constellation={cons} ==")
            print(s[cols].to_string(index=False))
    d = daily[(daily["country"] == "ALL") & (daily["constellation"] == "all")]
    print("\ncritical days (ALL, all satellites):", int(d["critical"].sum()))
    for c in COUNTRIES_ALL:
        t = tr.get(c, {})
        if t:
            print(f"trends core {c}: " + ", ".join(
                f"{k} {v['trend']} (p={v['p_value']:.3f}, slope={v['sen_slope_per_year']:.1f}/yr)"
                for k, v in t.items() if isinstance(v, dict)))


if __name__ == "__main__":
    main()
