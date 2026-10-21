"""Shared test fixtures for the test suite."""
import pytest
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Set environment variables before importing app modules
os.environ.setdefault("MONGODB_URL", "mongodb://localhost:27017")
os.environ.setdefault("DATABASE_NAME", "test_db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")
os.environ.setdefault("SESSION_SECRET_KEY", "test-session-key-only-never-use-in-production")
os.environ.setdefault("ACS_CONNECTION_STRING", "endpoint=https://test.communication.azure.com/;accesskey=dGVzdA==")
os.environ.setdefault("ACS_SENDER", "test@example.com")

# Now import app modules
from app.models.user import (
    UserInDB, UserRole, OAuthProvider, Language, SubscriptionStatus, BillingInterval
)
from app.models.stock import Direction, DailyPick, Prediction


def _synthetic_ohlcv(seed: int, days: int = 320, start_price: float = 100.0, drift: float = 0.0005):
    """Build a deterministic synthetic OHLCV frame for tests."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, 0.02, days)
    close = start_price * np.exp(np.cumsum(returns))
    dates = pd.bdate_range(end="2024-06-28", periods=days)

    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": np.round(close * (1 + rng.normal(0, 0.002, days)), 4),
            "high": np.round(close * (1 + np.abs(rng.normal(0, 0.006, days))), 4),
            "low": np.round(close * (1 - np.abs(rng.normal(0, 0.006, days))), 4),
            "close": np.round(close, 4),
            "volume": rng.integers(1_000_000, 9_000_000, days),
        }
    )


#: Synthetic index membership. ``BBB`` sits in both, mirroring the 88 real names
#: that are in the S&P 500 and the NASDAQ-100 at once.
SYNTHETIC_MEMBERSHIP = {"sp500": ["AAA", "BBB", "DDD"], "nasdaq100": ["BBB", "CCC"]}


def _write_membership(path, symbols, end_date: str = "2030-01-01") -> None:
    """Write one qlib-format instruments file."""
    path.write_text("\n".join(f"{s}\t2010-01-01\t{end_date}" for s in symbols))


@pytest.fixture
def synthetic_market(tmp_path):
    """Create an on-disk synthetic market dataset and a repository pointing at it.

    Writes the per-index membership files alongside the union so the universe
    the repository reports is index membership, exactly as it is in production.

    Yields a tuple of ``(repository, symbols)``.
    """
    from app.services.market_data import MarketDataRepository

    symbols = ["AAA", "BBB", "CCC", "DDD"]
    data_dir = tmp_path / "us_data"
    data_dir.mkdir(parents=True)

    for index, symbol in enumerate(symbols):
        frame = _synthetic_ohlcv(seed=index + 1, drift=0.001 - index * 0.0008)
        frame.to_csv(data_dir / f"{symbol}.csv", index=False)

    index_path = tmp_path / "us.txt"
    _write_membership(index_path, symbols)
    for index_name, members in SYNTHETIC_MEMBERSHIP.items():
        _write_membership(tmp_path / f"{index_name}.txt", members)

    repository = MarketDataRepository(data_dir=data_dir, index_path=index_path)
    return repository, symbols


@pytest.fixture
def mock_user() -> UserInDB:
    """Create a mock free user for testing."""
    return UserInDB(
        _id="test_user_id",
        email="test@example.com",
        name="Test User",
        role=UserRole.FREE,
        is_active=True,
        preferred_language=Language.ENGLISH,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        last_login=None,
        email_verified=False,
        premium_expires_at=None
    )


@pytest.fixture
def mock_premium_user() -> UserInDB:
    """Create a mock premium user for testing."""
    return UserInDB(
        _id="premium_user_id",
        email="premium@example.com",
        name="Premium User",
        role=UserRole.PREMIUM,
        is_active=True,
        preferred_language=Language.ENGLISH,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        last_login=datetime.now(timezone.utc),
        email_verified=True,
        premium_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        stripe_customer_id="cust_test123",
        stripe_subscription_id="sub_test123",
        subscription_status=SubscriptionStatus.ACTIVE,
        billing_interval=BillingInterval.MONTHLY
    )


@pytest.fixture
def mock_admin_user() -> UserInDB:
    """Create a mock admin user for testing."""
    return UserInDB(
        _id="admin_user_id",
        email="admin@example.com",
        name="Admin User",
        role=UserRole.ADMIN,
        is_active=True,
        preferred_language=Language.ENGLISH,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        last_login=datetime.now(timezone.utc),
        email_verified=True,
        premium_expires_at=None
    )


@pytest.fixture
def mock_expired_premium_user() -> UserInDB:
    """Create a mock user with expired premium subscription."""
    return UserInDB(
        _id="expired_premium_user_id",
        email="expired@example.com",
        name="Expired Premium User",
        role=UserRole.PREMIUM,
        is_active=True,
        preferred_language=Language.ENGLISH,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        last_login=datetime.now(timezone.utc),
        email_verified=True,
        premium_expires_at=datetime.now(timezone.utc) - timedelta(days=1)
    )


@pytest.fixture
def mock_daily_pick() -> DailyPick:
    """Create a mock daily pick for testing."""
    return DailyPick(
        instrument="AAPL",
        direction=Direction.RISING,
        score=0.85,
        strategy="momentum",
        date=datetime.now(timezone.utc)
    )


@pytest.fixture
def mock_prediction() -> Prediction:
    """Create a mock prediction for testing."""
    return Prediction(
        instrument="AAPL",
        us_rise_1d=0.75,
        us_rise_5d=0.82,
        us_decline_1d=0.25,
        us_decline_5d=0.18,
        date=datetime.now(timezone.utc)
    )


@pytest.fixture
def mock_db():
    """Create a mock database for testing."""
    mock = MagicMock()
    mock.users = MagicMock()
    mock.refresh_tokens = MagicMock()
    mock.referrals = MagicMock()
    mock.referral_clicks = MagicMock()
    mock.verification_codes = MagicMock()
    mock.rate_limits = MagicMock()
    mock.oauth_tokens = MagicMock()
    mock.login_codes = MagicMock()
    mock.user_sessions = MagicMock()
    mock.watchlists = MagicMock()
    mock.alerts = MagicMock()
    mock.screener_strategies = MagicMock()
    mock.picks = MagicMock()
    mock.predictions = MagicMock()
    mock.pivot_picks = MagicMock()
    mock.pivot_runs = MagicMock()
    mock.options_anomalies = MagicMock()
    return mock


@pytest.fixture
def mongo_auth_db(mock_db):
    """Stateful AsyncMock collections for atomic authentication operations."""
    from copy import deepcopy
    from bson import ObjectId
    from pymongo import ReturnDocument
    from pymongo.errors import DuplicateKeyError

    def matches(document, query):
        for key, expected in query.items():
            if key == "$or":
                if not any(matches(document, clause) for clause in expected):
                    return False
            elif isinstance(expected, dict):
                for operator, value in expected.items():
                    actual = document.get(key)
                    if operator == "$exists":
                        matched = (key in document) == value
                    elif operator == "$gt":
                        matched = actual is not None and actual > value
                    elif operator == "$lt":
                        matched = actual is not None and actual < value
                    elif operator == "$lte":
                        matched = actual is not None and actual <= value
                    else:
                        raise AssertionError(f"Unsupported operator: {operator}")
                    if not matched:
                        return False
            elif document.get(key) != expected:
                return False
        return True

    def configure(collection):
        documents = {}
        collection.documents = documents

        async def find_one(query):
            return deepcopy(next(
                (doc for doc in documents.values() if matches(doc, query)), None
            ))

        async def insert_one(document):
            document = deepcopy(document)
            document.setdefault("_id", ObjectId())
            if document["_id"] in documents:
                raise DuplicateKeyError("Duplicate _id", code=11000)
            documents[document["_id"]] = document
            return MagicMock(inserted_id=document["_id"])

        async def find_one_and_update(
            query, update, *, upsert=False, return_document=ReturnDocument.BEFORE
        ):
            document = next(
                (doc for doc in documents.values() if matches(doc, query)), None
            )
            previous = deepcopy(document)
            if document is None:
                if not upsert:
                    return None
                document = {
                    key: value for key, value in query.items()
                    if not key.startswith("$") and not isinstance(value, dict)
                }
                document.update(update.get("$setOnInsert", {}))
                result = await insert_one(document)
                document = documents[result.inserted_id]
            document.update(deepcopy(update.get("$set", {})))
            for field, value in update.get("$inc", {}).items():
                document[field] = document.get(field, 0) + value
            return deepcopy(document) if return_document else previous

        async def update_one(query, update):
            document = await find_one_and_update(
                query, update, return_document=ReturnDocument.AFTER
            )
            return MagicMock(matched_count=int(document is not None))

        async def delete_one(query):
            document = await find_one(query)
            if document is not None:
                del documents[document["_id"]]
            return MagicMock(deleted_count=int(document is not None))

        async def delete_many(query):
            identifiers = [
                key for key, document in documents.items()
                if matches(document, query)
            ]
            for key in identifiers:
                del documents[key]
            return MagicMock(deleted_count=len(identifiers))

        collection.find_one = AsyncMock(side_effect=find_one)
        collection.insert_one = AsyncMock(side_effect=insert_one)
        collection.find_one_and_update = AsyncMock(side_effect=find_one_and_update)
        collection.update_one = AsyncMock(side_effect=update_one)
        collection.delete_one = AsyncMock(side_effect=delete_one)
        collection.delete_many = AsyncMock(side_effect=delete_many)

    configure(mock_db.verification_codes)
    configure(mock_db.rate_limits)
    mock_db.users.update_one = AsyncMock()
    return mock_db
