"""Unit tests for the forward-path metrics service.

The bars are written by hand rather than generated, because the whole point of
these figures is the shape of the path and a random walk cannot assert one.
"""

import pandas as pd
import pytest

from app.models.pivot import PickDirection, PickEvent, PickKind, PivotPick
from app.services.path_metrics_service import PathMetricsService


def _service(tmp_path, bars: dict[str, pd.DataFrame]) -> PathMetricsService:
    data_dir = tmp_path / "us_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for symbol, frame in bars.items():
        frame.to_csv(data_dir / f"{symbol}.csv", index=False)
    service = PathMetricsService()
    service.stock_data_dir = data_dir
    service.clear_cache()
    return service


def _round_trip_bars() -> pd.DataFrame:
    """A path that peaks mid-window and gives it all back.

    Entry closes at 100. It runs to 110 on day five, then decays to 100 by day
    ten. The endpoint return is exactly zero, which is precisely the case an
    endpoint-only measure cannot distinguish from a signal that never moved.
    """
    dates = pd.bdate_range("2024-01-01", periods=11).strftime("%Y-%m-%d")
    closes = [100, 102, 104, 106, 108, 110, 108, 106, 104, 102, 100]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1_000_000] * 11,
        }
    )


def _pick(symbol: str = "AAA", date: str = "2024-01-01", direction: str = "long") -> PivotPick:
    return PivotPick(
        date=date,
        market="us",
        symbol=symbol,
        event=PickEvent.BUY_TURN,
        kind=PickKind.REVERSAL,
        direction=PickDirection(direction),
        rank=1,
        horizon=10,
        action="BUY",
        score=0.2,
        confidence=0.9,
    )


class TestPathOutcome:
    """The path a position took over its forward window."""

    def test_peak_mid_window_is_not_hidden_by_a_flat_endpoint(self, tmp_path):
        """A round trip reports its peak, where the endpoint return reports nothing."""
        service = _service(tmp_path, {"AAA": _round_trip_bars()})

        outcome = service.outcome_for("AAA", "2024-01-01", 10, "long")

        assert outcome is not None
        # High of the day-five bar is 111, against a 100 entry.
        assert outcome.max_gain == pytest.approx(0.11)
        assert outcome.days_to_max_gain == 5
        # The endpoint would have said 0.0; the path says otherwise.
        assert outcome.max_gain > 0

    def test_drawdown_is_measured_from_the_signal_close(self, tmp_path):
        """The worst point is reported even when the position recovers."""
        dates = pd.bdate_range("2024-01-01", periods=11).strftime("%Y-%m-%d")
        closes = [100, 95, 90, 88, 92, 96, 98, 99, 100, 100, 100]
        frame = pd.DataFrame(
            {
                "date": dates,
                "open": closes,
                "high": [c + 1 for c in closes],
                "low": [c - 1 for c in closes],
                "close": closes,
                "volume": [1_000_000] * 11,
            }
        )
        service = _service(tmp_path, {"AAA": frame})

        outcome = service.outcome_for("AAA", "2024-01-01", 10, "long")

        assert outcome is not None
        # Low of the day-three bar is 87.
        assert outcome.max_loss == pytest.approx(-0.13)
        assert outcome.days_to_max_loss == 3

    def test_short_reads_the_path_from_its_own_side(self, tmp_path):
        """A short gains when price falls, so its highs and lows swap roles."""
        service = _service(tmp_path, {"AAA": _round_trip_bars()})

        long_outcome = service.outcome_for("AAA", "2024-01-01", 10, "long")
        short_outcome = service.outcome_for("AAA", "2024-01-01", 10, "short")

        assert long_outcome is not None and short_outcome is not None
        assert short_outcome.max_gain == pytest.approx(-long_outcome.max_loss)
        assert short_outcome.max_loss == pytest.approx(-long_outcome.max_gain)
        assert short_outcome.days_to_max_gain == long_outcome.days_to_max_loss

    def test_signal_bar_is_excluded_from_its_own_window(self, tmp_path):
        """The entry bar is the entry, not an opportunity to exit."""
        dates = pd.bdate_range("2024-01-01", periods=11).strftime("%Y-%m-%d")
        closes = [100] * 11
        frame = pd.DataFrame(
            {
                "date": dates,
                "open": closes,
                # A huge spike on the signal bar only; the window must ignore it.
                "high": [500] + [100] * 10,
                "low": [1] + [100] * 10,
                "close": closes,
                "volume": [1_000_000] * 11,
            }
        )
        service = _service(tmp_path, {"AAA": frame})

        outcome = service.outcome_for("AAA", "2024-01-01", 10, "long")

        assert outcome is not None
        assert outcome.max_gain == pytest.approx(0.0)
        assert outcome.max_loss == pytest.approx(0.0)

    def test_partial_window_is_flagged_incomplete(self, tmp_path):
        """A horizon that has not elapsed must not read as a finished path."""
        service = _service(tmp_path, {"AAA": _round_trip_bars()})

        outcome = service.outcome_for("AAA", "2024-01-10", 10, "long")

        assert outcome is not None
        assert outcome.complete is False

    def test_complete_window_is_flagged_complete(self, tmp_path):
        service = _service(tmp_path, {"AAA": _round_trip_bars()})

        outcome = service.outcome_for("AAA", "2024-01-01", 10, "long")

        assert outcome is not None
        assert outcome.complete is True

    def test_last_bar_has_no_forward_window(self, tmp_path):
        service = _service(tmp_path, {"AAA": _round_trip_bars()})

        assert service.outcome_for("AAA", "2024-01-15", 10, "long") is None

    def test_date_that_is_not_a_trading_day_returns_none(self, tmp_path):
        """A signal must anchor on a real bar, not the next one along."""
        service = _service(tmp_path, {"AAA": _round_trip_bars()})

        assert service.outcome_for("AAA", "2024-01-06", 10, "long") is None

    def test_unknown_symbol_returns_none(self, tmp_path):
        service = _service(tmp_path, {"AAA": _round_trip_bars()})

        assert service.outcome_for("ZZZ", "2024-01-01", 10, "long") is None

    def test_malformed_date_returns_none(self, tmp_path):
        service = _service(tmp_path, {"AAA": _round_trip_bars()})

        assert service.outcome_for("AAA", "not-a-date", 10, "long") is None


class TestAttach:
    """Filling the metrics onto a list of picks."""

    def test_attach_fills_every_pick_with_bars(self, tmp_path):
        service = _service(tmp_path, {"AAA": _round_trip_bars()})
        picks = [_pick(date="2024-01-01"), _pick(date="2024-01-02")]

        result = service.attach(picks)

        assert all(p.max_gain is not None for p in result)
        assert all(p.max_loss is not None for p in result)
        assert all(p.path_complete is not None for p in result)

    def test_attach_leaves_picks_without_bars_untouched(self, tmp_path):
        """A missing price file is not zero, and must not be reported as zero."""
        service = _service(tmp_path, {"AAA": _round_trip_bars()})
        picks = [_pick(symbol="ZZZ")]

        result = service.attach(picks)

        assert result[0].max_gain is None
        assert result[0].max_loss is None

    def test_attach_reads_each_price_file_once(self, tmp_path):
        """Picks are grouped by symbol so a long history is not rescanned."""
        service = _service(tmp_path, {"AAA": _round_trip_bars()})
        calls: list[str] = []
        original = service._load_bars

        def counting(symbol: str):
            calls.append(symbol)
            return original(symbol)

        service._load_bars = counting  # type: ignore[method-assign]
        service.attach([_pick(date="2024-01-01"), _pick(date="2024-01-02"), _pick(date="2024-01-03")])

        assert calls == ["AAA"]

    def test_attach_on_empty_list(self, tmp_path):
        service = _service(tmp_path, {"AAA": _round_trip_bars()})

        assert service.attach([]) == []
