"""Tests for shared file snapshot paths."""

from pathlib import Path
from unittest.mock import patch

from app.config import settings
from app.services.snapshot_storage import market_snapshot_dir


def test_snapshot_root_is_inferred_from_stock_data_mount():
    with patch.object(settings, "predixlab_data_dir", None), patch.object(
        settings,
        "us_stock_data_dir",
        "/data/stock_data/us_data",
    ):
        path = market_snapshot_dir("US")

    assert path == Path("/data/snapshots/us")


def test_explicit_snapshot_root_wins():
    assert market_snapshot_dir("us", Path("/custom")) == Path(
        "/custom/snapshots/us"
    )