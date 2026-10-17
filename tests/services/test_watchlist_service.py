"""Unit tests for the watchlist and alert service."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from app.models.market import (
    Alert,
    AlertCreateRequest,
    AlertType,
    SymbolQuote,
)
from app.services.watchlist_service import (
    WatchlistService,
    _dedupe_symbols,
    evaluate_alert,
)


@pytest.fixture
def mock_analysis():
    analysis = MagicMock()
    analysis.get_quotes = AsyncMock(return_value=[SymbolQuote(symbol="AAA", close=100.0)])
    analysis.get_quote = AsyncMock(
        return_value=SymbolQuote(symbol="AAA", close=100.0, change_percent=0.05)
    )
    snapshot = MagicMock()
    snapshot.values = {"close": 100.0, "rsi_14": 75.0, "volume_ratio": 3.0, "sma_50": 90.0}
    analysis.get_indicator_snapshot = AsyncMock(return_value=snapshot)
    return analysis


@pytest.fixture
def service(mock_db, mock_analysis):
    svc = WatchlistService(analysis=mock_analysis)
    mock_db.watchlists = MagicMock()
    mock_db.alerts = MagicMock()
    svc.db = mock_db
    return svc


def _watchlist_doc(items=None, name="Default"):
    return {
        "_id": ObjectId(),
        "user_id": "test_user_id",
        "name": name,
        "items": items or [],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


class TestWatchlistCrud:
    @pytest.mark.asyncio
    async def test_list_watchlists(self, service):
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.to_list = AsyncMock(return_value=[_watchlist_doc()])
        service.db.watchlists.find = MagicMock(return_value=cursor)

        result = await service.list_watchlists("test_user_id")
        assert len(result) == 1
        assert result[0].name == "Default"

    @pytest.mark.asyncio
    async def test_create_watchlist_normalizes_symbols(self, service):
        service.db.watchlists.count_documents = AsyncMock(return_value=0)
        service.db.watchlists.find_one = AsyncMock(return_value=None)
        service.db.watchlists.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=ObjectId())
        )

        result = await service.create_watchlist("test_user_id", "Tech", ["aapl", " msft ", "AAPL"])
        assert [item.symbol for item in result.items] == ["AAPL", "MSFT"]

    @pytest.mark.asyncio
    async def test_create_watchlist_rejects_duplicate_name(self, service):
        service.db.watchlists.count_documents = AsyncMock(return_value=0)
        service.db.watchlists.find_one = AsyncMock(return_value=_watchlist_doc())

        with pytest.raises(ValueError, match="already exists"):
            await service.create_watchlist("test_user_id", "Default", [])

    @pytest.mark.asyncio
    async def test_create_watchlist_enforces_quota(self, service):
        service.db.watchlists.count_documents = AsyncMock(return_value=999)
        with pytest.raises(ValueError, match="at most"):
            await service.create_watchlist("test_user_id", "New", [])

    @pytest.mark.asyncio
    async def test_get_watchlist_invalid_id_returns_none(self, service):
        assert await service.get_watchlist("test_user_id", "not-an-object-id") is None

    @pytest.mark.asyncio
    async def test_delete_watchlist(self, service):
        service.db.watchlists.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
        assert await service.delete_watchlist("test_user_id", str(ObjectId())) is True

    @pytest.mark.asyncio
    async def test_delete_watchlist_invalid_id(self, service):
        assert await service.delete_watchlist("test_user_id", "bad") is False


class TestWatchlistSymbols:
    @pytest.mark.asyncio
    async def test_add_symbol_updates_document(self, service):
        doc = _watchlist_doc()
        service.db.watchlists.find_one = AsyncMock(return_value=doc)
        service.db.watchlists.update_one = AsyncMock()

        result = await service.add_symbol("test_user_id", str(doc["_id"]), "aapl")
        assert result is not None
        service.db.watchlists.update_one.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_existing_symbol_is_noop(self, service):
        doc = _watchlist_doc(items=[{"symbol": "AAPL", "note": None, "added_at": datetime.now(timezone.utc)}])
        service.db.watchlists.find_one = AsyncMock(return_value=doc)
        service.db.watchlists.update_one = AsyncMock()

        result = await service.add_symbol("test_user_id", str(doc["_id"]), "AAPL")
        assert [i.symbol for i in result.items] == ["AAPL"]
        service.db.watchlists.update_one.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_add_symbol_enforces_capacity(self, service):
        items = [
            {"symbol": f"S{i}", "note": None, "added_at": datetime.now(timezone.utc)}
            for i in range(100)
        ]
        doc = _watchlist_doc(items=items)
        service.db.watchlists.find_one = AsyncMock(return_value=doc)

        with pytest.raises(ValueError, match="at most"):
            await service.add_symbol("test_user_id", str(doc["_id"]), "NEW")

    @pytest.mark.asyncio
    async def test_add_symbol_unknown_watchlist(self, service):
        service.db.watchlists.find_one = AsyncMock(return_value=None)
        assert await service.add_symbol("test_user_id", str(ObjectId()), "AAPL") is None

    @pytest.mark.asyncio
    async def test_remove_symbol(self, service):
        doc = _watchlist_doc()
        service.db.watchlists.update_one = AsyncMock()
        service.db.watchlists.find_one = AsyncMock(return_value=doc)

        result = await service.remove_symbol("test_user_id", str(doc["_id"]), "aapl")
        assert result is not None
        service.db.watchlists.update_one.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_watchlist_quotes(self, service):
        doc = _watchlist_doc(items=[{"symbol": "AAA", "note": None, "added_at": datetime.now(timezone.utc)}])
        service.db.watchlists.find_one = AsyncMock(return_value=doc)

        watchlist, quotes = await service.get_watchlist_quotes("test_user_id", str(doc["_id"]))
        assert watchlist.name == "Default"
        assert quotes[0].symbol == "AAA"

    @pytest.mark.asyncio
    async def test_get_watchlist_quotes_unknown(self, service):
        service.db.watchlists.find_one = AsyncMock(return_value=None)
        assert await service.get_watchlist_quotes("test_user_id", str(ObjectId())) is None


class TestAlertCrud:
    @pytest.mark.asyncio
    async def test_create_alert(self, service):
        service.db.alerts.count_documents = AsyncMock(return_value=0)
        service.db.alerts.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))

        request = AlertCreateRequest(symbol="aapl", alert_type=AlertType.PRICE_ABOVE, threshold=200)
        alert = await service.create_alert("test_user_id", request)
        assert alert.symbol == "AAPL"
        assert alert.is_active is True

    @pytest.mark.asyncio
    async def test_create_alert_requires_threshold(self, service):
        service.db.alerts.count_documents = AsyncMock(return_value=0)
        request = AlertCreateRequest(symbol="AAPL", alert_type=AlertType.PRICE_ABOVE)
        with pytest.raises(ValueError, match="requires a threshold"):
            await service.create_alert("test_user_id", request)

    @pytest.mark.asyncio
    async def test_cross_alert_needs_no_threshold(self, service):
        service.db.alerts.count_documents = AsyncMock(return_value=0)
        service.db.alerts.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId()))
        request = AlertCreateRequest(symbol="AAPL", alert_type=AlertType.CROSS_ABOVE_SMA50)
        alert = await service.create_alert("test_user_id", request)
        assert alert.threshold is None

    @pytest.mark.asyncio
    async def test_create_alert_enforces_quota(self, service):
        service.db.alerts.count_documents = AsyncMock(return_value=1000)
        request = AlertCreateRequest(symbol="AAPL", alert_type=AlertType.PRICE_ABOVE, threshold=1)
        with pytest.raises(ValueError, match="at most"):
            await service.create_alert("test_user_id", request)

    @pytest.mark.asyncio
    async def test_delete_alert(self, service):
        service.db.alerts.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
        assert await service.delete_alert("test_user_id", str(ObjectId())) is True

    @pytest.mark.asyncio
    async def test_check_alerts_reports_triggers(self, service):
        alert_doc = {
            "_id": ObjectId(),
            "user_id": "test_user_id",
            "symbol": "AAA",
            "alert_type": AlertType.PRICE_ABOVE.value,
            "threshold": 50.0,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
        }
        cursor = MagicMock()
        cursor.sort.return_value = cursor
        cursor.to_list = AsyncMock(return_value=[alert_doc])
        service.db.alerts.find = MagicMock(return_value=cursor)
        service.db.alerts.update_one = AsyncMock()

        result = await service.check_alerts("test_user_id")
        assert result.checked == 1
        assert len(result.triggered) == 1
        assert result.triggered[0].current_value == 100.0


class TestAlertEvaluation:
    def _alert(self, alert_type, threshold=None):
        return Alert(
            id=str(ObjectId()), user_id="u", symbol="AAA",
            alert_type=alert_type, threshold=threshold,
        )

    def test_price_above_triggers(self):
        quote = SymbolQuote(symbol="AAA", close=110)
        result = evaluate_alert(self._alert(AlertType.PRICE_ABOVE, 100), {"close": 110}, quote)
        assert result[0] == 110

    def test_price_above_does_not_trigger(self):
        quote = SymbolQuote(symbol="AAA", close=90)
        assert evaluate_alert(self._alert(AlertType.PRICE_ABOVE, 100), {"close": 90}, quote) is None

    def test_price_below_triggers(self):
        quote = SymbolQuote(symbol="AAA", close=90)
        assert evaluate_alert(self._alert(AlertType.PRICE_BELOW, 100), {"close": 90}, quote) is not None

    def test_percent_change_up_triggers(self):
        quote = SymbolQuote(symbol="AAA", close=100, change_percent=0.08)
        result = evaluate_alert(self._alert(AlertType.PERCENT_CHANGE_UP, 0.05), {}, quote)
        assert result[0] == pytest.approx(0.08)

    def test_percent_change_down_triggers(self):
        quote = SymbolQuote(symbol="AAA", close=100, change_percent=-0.08)
        assert evaluate_alert(self._alert(AlertType.PERCENT_CHANGE_DOWN, -0.05), {}, quote) is not None

    def test_rsi_above_triggers(self):
        quote = SymbolQuote(symbol="AAA", close=100)
        assert evaluate_alert(self._alert(AlertType.RSI_ABOVE, 70), {"rsi_14": 80}, quote) is not None

    def test_rsi_below_does_not_trigger_when_high(self):
        quote = SymbolQuote(symbol="AAA", close=100)
        assert evaluate_alert(self._alert(AlertType.RSI_BELOW, 30), {"rsi_14": 80}, quote) is None

    def test_volume_spike_triggers(self):
        quote = SymbolQuote(symbol="AAA", close=100)
        assert evaluate_alert(self._alert(AlertType.VOLUME_SPIKE, 2.0), {"volume_ratio": 3.0}, quote) is not None

    def test_cross_above_sma50_triggers(self):
        quote = SymbolQuote(symbol="AAA", close=100)
        result = evaluate_alert(
            self._alert(AlertType.CROSS_ABOVE_SMA50), {"close": 100, "sma_50": 90}, quote
        )
        assert result is not None

    def test_cross_below_sma50_triggers(self):
        quote = SymbolQuote(symbol="AAA", close=80)
        result = evaluate_alert(
            self._alert(AlertType.CROSS_BELOW_SMA50), {"close": 80, "sma_50": 90}, quote
        )
        assert result is not None

    def test_missing_data_does_not_trigger(self):
        assert evaluate_alert(self._alert(AlertType.PRICE_ABOVE, 100), {}, None) is None


class TestHelpers:
    def test_dedupe_preserves_order_and_uppercases(self):
        assert _dedupe_symbols([" aapl ", "MSFT", "aapl", ""]) == ["AAPL", "MSFT"]

    def test_dedupe_empty_input(self):
        assert _dedupe_symbols([]) == []

    def test_object_id_invalid_returns_none(self):
        assert WatchlistService._object_id("nope") is None

    def test_object_id_valid(self):
        oid = ObjectId()
        assert WatchlistService._object_id(str(oid)) == oid
