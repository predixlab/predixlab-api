"""Unit tests for the market data repository."""
import pandas as pd
import pytest

from app.services.market_data import MarketDataRepository


class TestUniverse:
    def test_universe_from_index_file(self, synthetic_market):
        repository, symbols = synthetic_market
        assert repository.get_universe() == sorted(symbols)

    def test_universe_does_not_fall_back_to_directory_listing(
        self, synthetic_market, tmp_path
    ):
        """The price archive is not a universe.

        It keeps names long after they leave an index, so listing it put 173
        delisted and demoted symbols into every scan. Without membership the
        honest answer is nothing, not everything.
        """
        repository, _ = synthetic_market
        without_membership = MarketDataRepository(
            data_dir=repository.data_dir, index_path=tmp_path / "missing" / "us.txt"
        )
        assert without_membership.get_universe() == []

    def test_universe_excludes_symbols_whose_membership_ended(self, synthetic_market):
        """A removed constituent keeps its price file but leaves the universe."""
        repository, symbols = synthetic_market
        repository.index_path.write_text(
            "\n".join(
                f"{s}\t2010-01-01\t{'2020-06-30' if s == 'CCC' else '2030-01-01'}"
                for s in symbols
            )
        )
        repository.clear_cache()

        universe = repository.get_universe()
        assert "CCC" not in universe
        assert repository.get_frame("CCC") is not None

    def test_universe_falls_back_to_the_per_index_files(self, synthetic_market):
        """A drop without the union file still resolves to its members."""
        repository, _ = synthetic_market
        repository.index_path.unlink()
        repository.clear_cache()

        assert repository.get_universe() == ["AAA", "BBB", "CCC", "DDD"]

    def test_universe_is_cached(self, synthetic_market):
        repository, _ = synthetic_market
        first = repository.get_universe()
        assert repository.get_universe() is first

    def test_universe_empty_when_nothing_available(self, tmp_path):
        repository = MarketDataRepository(
            data_dir=tmp_path / "nope", index_path=tmp_path / "nope" / "us.txt"
        )
        assert repository.get_universe() == []


class TestFrames:
    def test_get_frame_is_sorted_ascending(self, synthetic_market):
        repository, symbols = synthetic_market
        frame = repository.get_frame(symbols[0])
        assert frame is not None
        assert frame["date"].is_monotonic_increasing

    def test_get_frame_has_canonical_columns(self, synthetic_market):
        repository, symbols = synthetic_market
        frame = repository.get_frame(symbols[0])
        assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume"]

    def test_get_frame_is_case_insensitive(self, synthetic_market):
        repository, symbols = synthetic_market
        assert repository.get_frame(symbols[0].lower()) is not None

    def test_get_frame_returns_none_for_unknown_symbol(self, synthetic_market):
        repository, _ = synthetic_market
        assert repository.get_frame("ZZZZ") is None

    def test_missing_symbol_result_is_cached(self, synthetic_market):
        repository, _ = synthetic_market
        repository.get_frame("ZZZZ")
        assert repository.get_frame("ZZZZ") is None

    def test_get_tail_limits_rows(self, synthetic_market):
        repository, symbols = synthetic_market
        assert len(repository.get_tail(symbols[0], 30)) == 30

    def test_get_frames_skips_unknown_symbols(self, synthetic_market):
        repository, symbols = synthetic_market
        frames = repository.get_frames(symbols + ["ZZZZ"], bars=50)
        assert set(frames) == set(symbols)

    def test_get_close_matrix_aligns_symbols(self, synthetic_market):
        repository, symbols = synthetic_market
        matrix = repository.get_close_matrix(symbols, bars=60)
        assert list(matrix.columns) == symbols
        assert len(matrix) == 60

    def test_get_close_matrix_empty_for_unknown_symbols(self, synthetic_market):
        repository, _ = synthetic_market
        assert repository.get_close_matrix(["ZZZZ"]).empty

    def test_get_latest_date_format(self, synthetic_market):
        repository, symbols = synthetic_market
        latest = repository.get_latest_date(symbols[0])
        assert pd.Timestamp(latest).strftime("%Y-%m-%d") == latest

    def test_clear_cache_resets_state(self, synthetic_market):
        repository, symbols = synthetic_market
        repository.get_frame(symbols[0])
        repository.clear_cache()
        assert repository.frame_cache.get_stats()["total_entries"] == 0


class TestNormalization:
    def test_normalize_rejects_missing_columns(self):
        assert MarketDataRepository._normalize(pd.DataFrame({"close": [1.0]})) is None

    def test_normalize_handles_empty_frame(self):
        assert MarketDataRepository._normalize(pd.DataFrame()) is None

    def test_normalize_drops_duplicate_dates(self):
        frame = pd.DataFrame(
            {
                "date": ["2024-01-01", "2024-01-01", "2024-01-02"],
                "open": [1.0, 1.0, 2.0],
                "high": [1.0, 1.0, 2.0],
                "low": [1.0, 1.0, 2.0],
                "close": [1.0, 1.5, 2.0],
                "volume": [10, 20, 30],
            }
        )
        result = MarketDataRepository._normalize(frame)
        assert len(result) == 2
        assert result["close"].iloc[0] == 1.5

    def test_normalize_lowercases_column_names(self):
        frame = pd.DataFrame(
            {
                "Date": ["2024-01-01"],
                "Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [10],
            }
        )
        result = MarketDataRepository._normalize(frame)
        assert list(result.columns) == ["date", "open", "high", "low", "close", "volume"]

    def test_normalize_drops_rows_without_close(self):
        frame = pd.DataFrame(
            {
                "date": ["2024-01-01", "2024-01-02"],
                "open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0],
                "close": [None, 2.0], "volume": [10, 20],
            }
        )
        assert len(MarketDataRepository._normalize(frame)) == 1
