"""Tests for the burning calendar: zero-fill, leap alignment, season stats, critical days, trends."""

from datetime import date

import numpy as np
import pandas as pd

from smogwatch import calendar as cal


def events_from_counts(counts: dict[str, int], country: str = "PK", seen_by: str = "VIIRS") -> pd.DataFrame:
    rows = []
    for d, n in counts.items():
        for i in range(n):
            rows.append({"event_id": f"{d}_{i}", "date": pd.Timestamp(d).date(), "country": country,
                         "seen_by": seen_by, "sum_frp_mw": 2.0, "n_detections": 2})
    return pd.DataFrame(rows)


def test_aligned_doy_same_date_same_column():
    s = pd.Series(pd.to_datetime(["2023-11-01", "2024-11-01", "2024-02-29", "2024-02-28"]))
    assert cal.aligned_doy(s).tolist() == [305, 305, 59, 59]


def test_season_dates_truncated_at_today():
    d = cal.season_dates([2026], "main", today=date(2026, 9, 20))
    assert d.min() == pd.Timestamp("2026-09-15") and d.max() == pd.Timestamp("2026-09-20") and len(d) == 6


def test_daily_counts_zero_fill_only_within_given_dates():
    ev = events_from_counts({"2024-10-01": 3, "2024-10-03": 1})
    dates = pd.date_range("2024-10-01", "2024-10-04")
    d = cal.daily_counts(ev, dates)
    all_ = d[d["country"] == "ALL"].set_index("date")["n_events"]
    assert all_.tolist() == [3, 0, 1, 0]
    assert d[d["country"] == "IN"]["n_events"].sum() == 0
    assert d[d["country"] == "PK"]["n_events"].sum() == 4
    assert d.loc[d["country"] == "ALL", "n_detections"].sum() == 8


def test_calendar_matrix_unfetched_days_are_nan():
    ev = events_from_counts({"2023-10-01": 1, "2024-10-02": 2})
    dates = pd.DatetimeIndex(["2023-10-01", "2024-10-01", "2024-10-02"])
    m = cal.calendar_matrix(cal.daily_counts(ev, dates))
    assert m.loc[2024, 274] == 0 and m.loc[2024, 275] == 2  # 1 Oct = 274, 2 Oct = 275
    assert np.isnan(m.loc[2023, 275])  # 2023-10-02 was never fetched


def test_season_stats_start_peak_end():
    counts = {f"2024-10-{d:02d}": 0 for d in range(1, 21)}
    counts.update({"2024-10-05": 5, "2024-10-06": 6, "2024-10-07": 20, "2024-10-08": 7, "2024-10-15": 9})
    ev = events_from_counts({k: v for k, v in counts.items() if v})
    daily = cal.daily_counts(ev, pd.date_range("2024-10-01", "2024-10-20"))
    st = cal.season_stats(daily, threshold=1).iloc[0]
    assert st["start"] == date(2024, 10, 5) and st["end"] == date(2024, 10, 8)  # 15 Oct is a lone day
    assert st["peak_date"] == date(2024, 10, 7) and st["peak_count"] == 20
    assert st["total_events"] == 47
    assert st["p50_date"] == date(2024, 10, 7)
    assert not st["complete"]  # only 20 of the 92 season days


def test_critical_days_use_prior_years_only():
    counts = {}
    for y in (2020, 2021, 2022):
        counts.update({f"{y}-10-{d:02d}": 5 for d in range(1, 11)})
    counts.update({f"2023-10-{d:02d}": 5 for d in range(1, 11)})
    counts["2023-10-05"] = 50
    ev = events_from_counts(counts)
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(list(counts))))
    flagged = cal.critical_days(cal.daily_counts(ev, dates))
    flagged = flagged[flagged["country"] == "ALL"]
    crit = flagged.loc[flagged["critical"], "date"].dt.date.tolist()
    assert crit == [date(2023, 10, 5)]
    assert flagged.loc[flagged["year"] < 2023, "clim_p90"].isna().all()  # fewer than 3 prior years


def test_trends_detects_increase():
    stats = pd.DataFrame({
        "year": range(2012, 2022), "total_events": [100 + 50 * i for i in range(10)],
        "total_frp_mw": [1000.0 + i for i in range(10)],
        "peak_date": [date(y, 11, 1) for y in range(2012, 2022)],
        "p50_date": [date(y, 11, 1) for y in range(2012, 2022)],
        "complete": True,
    })
    tr = cal.trends(stats)
    assert tr["total_events"]["trend"] == "increasing"
    assert abs(tr["total_events"]["sen_slope_per_year"] - 50) < 1e-9
    assert tr["n_seasons"] == 10


def test_same_date_comparison():
    ev = events_from_counts({"2025-10-01": 10, "2025-11-20": 99, "2026-10-01": 15})
    daily = cal.daily_counts(ev, pd.date_range("2025-09-15", "2025-12-15").append(
        pd.date_range("2026-09-15", "2026-11-15")))
    r = cal.same_date_comparison(daily, date(2026, 11, 15))
    assert r == {"this_year": 15, "last_year": 10, "change_pct": 50.0}
