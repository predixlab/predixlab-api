"""Unit tests for index membership resolution."""
import pytest

from app.services.universe_service import UniverseService


@pytest.fixture
def universe(synthetic_market):
    """The service the repository built from the synthetic membership files."""
    repository, _ = synthetic_market
    return repository.universe


class TestMembership:
    def test_union_is_the_default_universe(self, universe):
        assert universe.get_universe() == ["AAA", "BBB", "CCC", "DDD"]

    def test_index_members_are_read_per_file(self, universe):
        assert universe.get_index_members("sp500") == ["AAA", "BBB", "DDD"]
        assert universe.get_index_members("nasdaq100") == ["BBB", "CCC"]

    def test_a_symbol_can_belong_to_both_indices(self, universe):
        """88 of the 516 real US names do."""
        assert universe.get_membership()["BBB"] == ["sp500", "nasdaq100"]

    def test_membership_omits_symbols_in_no_index(self, universe, synthetic_market):
        repository, _ = synthetic_market
        (repository.index_path.parent / "sp500.txt").write_text("AAA\t2010-01-01\t2030-01-01")
        universe.clear_cache()

        assert "DDD" not in universe.get_membership()

    def test_unknown_index_returns_nothing(self, universe):
        """Widening the scan on a typo would be worse than returning nothing."""
        assert universe.get_index_members("russell2000") == []


class TestCurrentMembership:
    def test_ended_membership_is_excluded(self, universe, synthetic_market):
        repository, _ = synthetic_market
        repository.index_path.write_text(
            "AAA\t2010-01-01\t2030-01-01\nZZZ\t2010-01-01\t2020-06-30"
        )
        universe.clear_cache()

        assert universe.get_universe() == ["AAA"]

    def test_membership_ending_today_is_still_current(self, universe, synthetic_market):
        """Removal is effective at the close, so the last day still counts."""
        import pandas as pd

        repository, _ = synthetic_market
        today = pd.Timestamp.today().strftime("%Y-%m-%d")
        repository.index_path.write_text(f"AAA\t2010-01-01\t{today}")
        universe.clear_cache()

        assert universe.get_universe() == ["AAA"]

    def test_missing_end_date_is_treated_as_open(self, universe, synthetic_market):
        """An older drop wrote a bare symbol list; degrade, do not empty."""
        repository, _ = synthetic_market
        repository.index_path.write_text("AAA\nBBB")
        universe.clear_cache()

        assert universe.get_universe() == ["AAA", "BBB"]

    def test_symbols_are_normalised(self, universe, synthetic_market):
        repository, _ = synthetic_market
        repository.index_path.write_text(" aaa \t2010-01-01\t2030-01-01\n\t\t")
        universe.clear_cache()

        assert universe.get_universe() == ["AAA"]


class TestResolve:
    def test_none_and_all_select_the_union(self, universe):
        assert universe.resolve(None) == universe.get_universe()
        assert universe.resolve("all") == universe.get_universe()

    def test_named_index_selects_its_members(self, universe):
        assert universe.resolve("nasdaq100") == ["BBB", "CCC"]

    def test_resolve_is_case_insensitive(self, universe):
        assert universe.resolve("SP500") == ["AAA", "BBB", "DDD"]


class TestFallbacks:
    def test_no_files_yields_an_empty_universe(self, tmp_path):
        service = UniverseService(index_path=tmp_path / "nothing" / "us.txt")
        assert service.get_universe() == []

    def test_unreadable_union_falls_back_to_the_index_files(
        self, universe, synthetic_market
    ):
        repository, _ = synthetic_market
        repository.index_path.unlink()
        universe.clear_cache()

        assert universe.get_universe() == ["AAA", "BBB", "CCC", "DDD"]
