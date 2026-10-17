"""Shared paths for collector-produced file-backed snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.config import settings


def market_snapshot_dir(market: str, root: Optional[Path] = None) -> Path:
    configured_root = root or settings.predixlab_data_dir
    if configured_root is not None:
        data_root = Path(configured_root).expanduser()
    else:
        stock_data_dir = Path(settings.us_stock_data_dir).expanduser()
        data_root = stock_data_dir.parents[1]
    return data_root / "snapshots" / market.lower()
