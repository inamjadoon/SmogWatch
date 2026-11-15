"""Offline tests for fetch_firms: URL building, chunking, parsing, caching and SP/NRT planning."""

from datetime import date

import pandas as pd
import pytest

from smogwatch import fetch_firms as ff

CSV = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,"
    "confidence,version,bright_ti5,frp,daynight\n"
    "30.1,74.2,330.5,0.4,0.37,2025-10-20,0812,N,VIIRS,n,2.0NRT,290.1,5.2,D\n"
)


class FakeResp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text, self.status_code = text, status

    def raise_for_status(self) -> None:
        pass


class FakeSession:
    def __init__(self, text: str = CSV) -> None:
        self.text, self.calls = text, []

    def get(self, url: str, timeout: int = 0) -> FakeResp:
        self.calls.append(url)
        return FakeResp(self.text)


def test_area_url_format() -> None:
    url = ff.area_url("KEY", "VIIRS_SNPP_SP", date(2020, 10, 1), 5)
    assert url == (
        "https://firms.modaps.eosdis.nasa.gov/api/area/csv/KEY/VIIRS_SNPP_SP/"
        "69,27.5,77.8,33/5/2020-10-01"
    )


def test_area_url_rejects_long_range() -> None:
    with pytest.raises(ValueError):
        ff.area_url("KEY", "MODIS_SP", date(2020, 1, 1), 6)


def test_date_chunks_cover_range_exactly() -> None:
    chunks = list(ff.date_chunks(date(2025, 9, 15), date(2025, 9, 26)))
    assert chunks == [(date(2025, 9, 15), 5), (date(2025, 9, 20), 5), (date(2025, 9, 25), 2)]


def test_parse_error_message_raises() -> None:
    with pytest.raises(ff.FirmsError):
        ff.parse_firms_csv("Invalid MAP_KEY.")


def test_parse_header_only_is_empty() -> None:
    assert ff.parse_firms_csv(CSV.splitlines()[0]).empty


def test_parse_keeps_acq_time_as_string() -> None:
    df = ff.parse_firms_csv(CSV)
    assert df.loc[0, "acq_time"] == "0812"


def test_fetch_area_uses_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ff.config, "FIRMS_SLEEP_S", 0)
    s = FakeSession()
    kw = dict(session=s, map_key="KEY", raw_dir=tmp_path, today=date(2026, 11, 15))
    df1 = ff.fetch_area("VIIRS_SNPP_SP", date(2020, 10, 1), 5, **kw)
    df2 = ff.fetch_area("VIIRS_SNPP_SP", date(2020, 10, 1), 5, **kw)
    assert len(s.calls) == 1 and len(df1) == len(df2) == 1
    assert (df1["source"] == "VIIRS_SNPP_SP").all()


def test_fetch_area_refetches_fresh_chunks(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ff.config, "FIRMS_SLEEP_S", 0)
    s = FakeSession()
    kw = dict(session=s, map_key="KEY", raw_dir=tmp_path, today=date(2026, 11, 15))
    ff.fetch_area("VIIRS_SNPP_NRT", date(2026, 11, 13), 3, **kw)
    ff.fetch_area("VIIRS_SNPP_NRT", date(2026, 11, 13), 3, **kw)
    assert len(s.calls) == 2


def test_error_response_not_cached(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ff.config, "FIRMS_SLEEP_S", 0)
    with pytest.raises(ff.FirmsError):
        ff.fetch_area("MODIS_SP", date(2020, 10, 1), 5, session=FakeSession("Invalid MAP_KEY."),
                      map_key="BAD", raw_dir=tmp_path, today=date(2026, 11, 15))
    assert not any(tmp_path.rglob("*.csv"))


def test_plan_requests_sp_then_nrt_and_respects_min_date() -> None:
    avail = pd.DataFrame({
        "data_id": ["VIIRS_SNPP_SP", "VIIRS_SNPP_NRT", "VIIRS_NOAA21_SP", "VIIRS_NOAA21_NRT"],
        "min_date": [date(2012, 1, 20), date(2025, 6, 1), date(2024, 1, 17), date(2025, 6, 1)],
        "max_date": [date(2025, 9, 30), date(2025, 12, 1), date(2025, 9, 30), date(2025, 12, 1)],
    })
    plan = ff.plan_requests(date(2025, 9, 15), date(2025, 10, 10), avail,
                            families=["VIIRS_SNPP", "VIIRS_NOAA21"])
    assert plan == [
        ("VIIRS_SNPP_SP", date(2025, 9, 15), date(2025, 9, 30)),
        ("VIIRS_SNPP_NRT", date(2025, 10, 1), date(2025, 10, 10)),
        ("VIIRS_NOAA21_SP", date(2025, 9, 15), date(2025, 9, 30)),
        ("VIIRS_NOAA21_NRT", date(2025, 10, 1), date(2025, 10, 10)),
    ]
    # Before NOAA-21 existed, nothing is planned for it
    assert ff.plan_requests(date(2015, 10, 1), date(2015, 10, 5), avail, families=["VIIRS_NOAA21"]) == []
