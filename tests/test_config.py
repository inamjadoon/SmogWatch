"""Smoke tests for config: the package imports and constants are self-consistent."""

from datetime import date

from smogwatch import config


def test_bbox_is_ordered() -> None:
    w, s, e, n = config.BBOX
    assert w < e and s < n


def test_lahore_inside_bbox() -> None:
    lat, lon = config.LAHORE
    w, s, e, n = config.BBOX
    assert w <= lon <= e and s <= lat <= n


def test_season_bounds_main() -> None:
    assert config.season_bounds(2024, "main") == (date(2024, 9, 15), date(2024, 12, 15))


def test_paths_point_into_repo() -> None:
    assert (config.ROOT_DIR / "pyproject.toml").exists()
    assert config.RAW_DIR.parent == config.PROCESSED_DIR.parent == config.DATA_DIR
