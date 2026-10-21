import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
import logging
from pathlib import Path

from app.database import get_database
from app.models.stock import (
    StockData, Direction, BacktestSignal,
    BacktestPerformance, BacktestResult, StockDataResponse,
    DailyPicksResponse, PredictionsResponse, AvailableDatesResponse,
    TradingCalendarResponse
)
from app.config import settings
from app.core.security import can_access_recent_data, is_premium_user
from app.services.cache import InMemoryCache, CacheKeyBuilder
from app.services.picks_service import picks_service
from app.services.predictions_service import predictions_service
from app.services.universe_service import universe_service

logger = logging.getLogger(__name__)

class StockService:
    def __init__(self):
        self.db = None
        self.stock_data_dir = Path(settings.us_stock_data_dir).expanduser()
        if settings.us_reference_data_dir is not None:
            self.reference_data_dir = Path(settings.us_reference_data_dir).expanduser()
        else:
            data_root = (
                Path(settings.predixlab_data_dir).expanduser()
                if settings.predixlab_data_dir is not None
                else self.stock_data_dir.parents[1]
            )
            self.reference_data_dir = data_root / "reference_data" / "us_indices"
        self.us_index_path = Path(settings.us_index_path).expanduser()
        self.us_calendar_path = Path(settings.us_calendar_path).expanduser()
        self.cache = InMemoryCache(default_ttl_minutes=30)

    async def initialize(self):
        """Initialize database connection"""
        self.db = await get_database()
        await picks_service.initialize()
        await predictions_service.initialize()

    def _get_nth_trading_day_after(self, start_date: str, n: int, trading_dates: set) -> Optional[str]:
        """Get the nth trading day after the given start date

        Args:
            start_date: Start date in YYYY-MM-DD format
            n: Number of trading days to advance
            trading_dates: Set of trading dates in YYYY-MM-DD format

        Returns:
            The nth trading day after start_date in YYYY-MM-DD format, or None if not found
        """
        try:
            current_date = datetime.strptime(start_date, '%Y-%m-%d')
        except ValueError:
            logger.error(f"Invalid start_date format: {start_date}")
            return None

        trading_days_found = 0
        max_days_to_search = 30  # Prevent infinite loop, 30 calendar days should be enough to find 10 trading days
        days_searched = 0

        while trading_days_found < n and days_searched < max_days_to_search:
            days_searched += 1
            current_date += timedelta(days=1)
            current_date_str = current_date.strftime('%Y-%m-%d')

            if current_date_str in trading_dates:
                trading_days_found += 1

                if trading_days_found == n:
                    return current_date_str

        # If we couldn't find the nth trading day within reasonable time
        logger.warning(f"Could not find {n}th trading day after {start_date} within {max_days_to_search} calendar days")
        return None

    def _validate_and_convert_period(self, period: Optional[str]) -> Optional[int]:
        """Validate and convert period string to days

        Args:
            period: Period string (1m, 3m, 9m, 1y, 2y, 3y)

        Returns:
            Number of days, or None if no period specified

        Raises:
            ValueError: If period format is invalid
        """
        if period is None:
            return None

        period = period.lower().strip()

        # Define valid periods and their corresponding days
        period_mapping = {
            '1m': 30,     # 1 month ≈ 30 days
            '3m': 90,     # 3 months ≈ 90 days
            '6m': 180,    # 6 months ≈ 180 days
            '9m': 270,    # 9 months ≈ 270 days
            '1y': 365,    # 1 year = 365 days
            '2y': 730,    # 2 years = 730 days
            '3y': 1095    # 3 years = 1095 days
        }

        if period not in period_mapping:
            valid_periods = ', '.join(period_mapping.keys())
            raise ValueError(f"Invalid period '{period}'. Valid periods are: {valid_periods}")

        return period_mapping[period]

    def _apply_data_filters(self, stock_response: StockDataResponse, start_datetime: Optional[datetime], end_datetime: Optional[datetime], period_days: Optional[int], period: Optional[str]) -> StockDataResponse:
        """Apply date filters to stock data response

        Args:
            stock_response: Full stock data response
            start_datetime: Start date filter
            end_datetime: End date filter
            period_days: Period in days, counted back from the most recent bar
            period: Original period string for logging

        Returns:
            Filtered StockDataResponse
        """
        # If no filters specified, return all data
        if not start_datetime and not end_datetime and period_days is None:
            return stock_response

        filtered_data = stock_response.data

        # Apply date range filter (priority over period)
        if start_datetime or end_datetime:
            if start_datetime and end_datetime:
                # Both start and end dates specified
                filtered_data = [
                    data for data in filtered_data
                    if start_datetime <= data.date <= end_datetime
                ]
                logger.debug(f"Filtered stock data for {stock_response.symbol} from {start_datetime.strftime('%Y-%m-%d')} to {end_datetime.strftime('%Y-%m-%d')}")
            elif start_datetime:
                # Only start date specified
                filtered_data = [
                    data for data in filtered_data
                    if data.date >= start_datetime
                ]
                logger.debug(f"Filtered stock data for {stock_response.symbol} from {start_datetime.strftime('%Y-%m-%d')} onwards")
            elif end_datetime:
                # Only end date specified
                filtered_data = [
                    data for data in filtered_data
                    if data.date <= end_datetime
                ]
                logger.debug(f"Filtered stock data for {stock_response.symbol} up to {end_datetime.strftime('%Y-%m-%d')}")
        # Apply period filter if no date range specified.
        #
        # The window is anchored on the most recent bar in the series, not on
        # wall-clock now. Price data always lags real time (weekends, holidays,
        # an ingestion that has not run yet), and anchoring on now silently
        # shrinks the window or empties it entirely once the lag exceeds the
        # period. "3m" means the last three months of trading, not the trading
        # that happened to fall in the last three calendar months.
        elif period_days is not None:
            latest_date = max((data.date for data in filtered_data), default=None)
            if latest_date is not None:
                cutoff_date = latest_date - timedelta(days=period_days)
                filtered_data = [
                    data for data in filtered_data
                    if data.date >= cutoff_date
                ]
                logger.debug(
                    f"Filtered stock data for {stock_response.symbol} to the {period_days} days "
                    f"({period}) ending {latest_date.strftime('%Y-%m-%d')}"
                )

        return StockDataResponse(
            symbol=stock_response.symbol,
            data=filtered_data,
            total_records=len(filtered_data)
        )

    def _load_data_file(self, file_path: Path) -> Optional[pd.DataFrame]:
        """Load CSV or Parquet file"""
        try:
            if file_path.suffix == '.csv':
                return pd.read_csv(file_path)
            elif file_path.suffix == '.parquet':
                return pd.read_parquet(file_path)
        except Exception as e:
            logger.error(f"Failed to load file {file_path}: {e}")
        return None


    async def get_available_dates(self, limit: Optional[int] = None) -> AvailableDatesResponse:
        """Get available dates for picks and predictions"""
        # Include limit in cache key to ensure proper caching
        cache_key = CacheKeyBuilder.available_dates(limit=limit)

        # Try to get from cache first
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Retrieved available dates from cache (limit: {limit})")
            return cached_result

        # Get pick dates from MongoDB with limit
        pick_dates = await picks_service.get_available_dates(limit=limit)

        result = AvailableDatesResponse(
            dates=pick_dates,
        )

        # Cache the result for 30 minutes
        self.cache.set(cache_key, result)
        logger.debug(f"Cached available dates (limit: {limit})")

        return result

    async def get_daily_picks(self, date: str, direction: Optional[Direction] = None, user_role: str = None, premium_expires_at: Optional[datetime] = None) -> DailyPicksResponse:
        """Get daily picks for specified date and optional direction

        Args:
            date: Date in YYYY-MM-DD format
            direction: Optional direction filter (rising/falling)
            user_role: User role for permission checking
            premium_expires_at: Premium expiration date

        Returns:
            DailyPicksResponse with both rising and falling picks.
            If direction is specified, the other direction will be empty.
        """
        # Check data access permissions
        try:
            data_date = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("Invalid date format. Use YYYY-MM-DD")

        if user_role and not can_access_recent_data(user_role, premium_expires_at, data_date):
            raise PermissionError("Access to recent data requires premium membership")

        # Create cache key based on whether direction is specified
        cache_key = CacheKeyBuilder.daily_picks(date=date, direction=direction)
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Retrieved daily picks from cache: {date}, direction: {direction.value if direction else 'all'}")
            return cached_result

        if direction is not None:
            # Get only the specified direction picks, other direction should be empty
            picks = await picks_service.get_picks_by_date(date, direction)

            if direction == Direction.RISING:
                rising_picks = picks
                falling_picks = []
            else:  # Direction.FALLING
                rising_picks = []
                falling_picks = picks
        else:
            # Get all picks for the date and separate by direction
            all_picks = await picks_service.get_picks_by_date(date)
            rising_picks = [pick for pick in all_picks if pick.direction == Direction.RISING]
            falling_picks = [pick for pick in all_picks if pick.direction == Direction.FALLING]

        result = DailyPicksResponse(
            date=date,
            risings=rising_picks,
            fallings=falling_picks
        )

        # Cache the result
        self.cache.set(cache_key, result)
        logger.debug(f"Cached daily picks: {date}, direction: {direction.value if direction else 'all'}")

        return result

    async def get_latest_picks(self, user_role: str, premium_expires_at: Optional[datetime] = None) -> DailyPicksResponse:
        """Get latest picks based on user membership status

        Returns latest trading day picks for premium users,
        or 10 days ago picks for free users
        """
        # Use picks_service to get latest picks based on user membership
        is_premium = is_premium_user(user_role, premium_expires_at)

        # Check cache first
        cache_key = f"latest_picks:{'premium' if is_premium else 'free'}"
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Retrieved latest picks from cache for user role: {user_role}")
            return cached_result

        # Get latest picks using the picks_service method
        latest_picks = await picks_service.get_latest_picks("us", is_premium=is_premium)

        if not latest_picks:
            result = DailyPicksResponse(date="", risings=[], fallings=[])
            self.cache.set(cache_key, result)
            return result

        # Get the date from the first pick
        target_date = latest_picks[0].date.strftime('%Y-%m-%d')

        # Separate picks by direction
        rising_picks = [pick for pick in latest_picks if pick.direction == Direction.RISING]
        falling_picks = [pick for pick in latest_picks if pick.direction == Direction.FALLING]

        result = DailyPicksResponse(
            date=target_date,
            risings=rising_picks,
            fallings=falling_picks
        )

        # Cache the result for 30 minutes
        self.cache.set(cache_key, result)
        user_type = "premium" if is_premium else "free"
        logger.debug(f"Cached latest picks for {user_type} user, date: {target_date}")

        return result

    async def get_predictions(self, date: str, user_role: str, premium_expires_at: Optional[datetime] = None) -> PredictionsResponse:
        """Get predictions for specified date"""
        # Check data access permissions
        try:
            data_date = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("Invalid date format. Use YYYY-MM-DD")

        if not can_access_recent_data(user_role, premium_expires_at, data_date):
            raise PermissionError("Access to recent data requires premium membership")

        # Check cache first
        cache_key = CacheKeyBuilder.predictions(date)
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Retrieved predictions from cache: {date}")
            return cached_result

        # Get predictions from MongoDB
        predictions = await predictions_service.get_predictions_by_date(date)

        result = PredictionsResponse(
            date=date,
            predictions=predictions,
            total_count=len(predictions)
        )

        # Cache the result
        self.cache.set(cache_key, result)
        logger.debug(f"Cached predictions: {date}")

        return result

    async def get_stock_data(self, symbol: str, start_date: Optional[str] = None, end_date: Optional[str] = None, period: Optional[str] = None) -> Optional[StockDataResponse]:
        """Get stock historical data

        Args:
            symbol: Stock symbol
            start_date: Start date in YYYY-MM-DD format (takes priority over period)
            end_date: End date in YYYY-MM-DD format (takes priority over period)
            period: Period for historical data (1m, 3m, 6m, 9m, 1y, 2y, 3y)

        Returns:
            StockDataResponse with filtered data based on parameters
        """
        # Validate and parse date parameters
        start_datetime = None
        end_datetime = None

        if start_date or end_date:
            # Validate date format and parse
            if start_date:
                try:
                    start_datetime = datetime.strptime(start_date, '%Y-%m-%d')
                except ValueError:
                    raise ValueError("Invalid start_date format. Use YYYY-MM-DD")

            if end_date:
                try:
                    end_datetime = datetime.strptime(end_date, '%Y-%m-%d')
                except ValueError:
                    raise ValueError("Invalid end_date format. Use YYYY-MM-DD")

            # Validate date range
            if start_datetime and end_datetime and start_datetime > end_datetime:
                raise ValueError("start_date must be earlier than or equal to end_date")

        # Validate period parameter (only if no date range specified)
        period_days = None
        if not (start_date or end_date) and period:
            period_days = self._validate_and_convert_period(period)

        # Use standard cache key (without filters) to cache all data
        cache_key = CacheKeyBuilder.stock_data(symbol)
        cached_result = self.cache.get(cache_key)

        # If we have cached data, apply filters and return
        if cached_result is not None:
            logger.debug(f"Retrieved stock data from cache: {symbol}")
            return self._apply_data_filters(cached_result, start_datetime, end_datetime, period_days, period)

        # Try US data first
        us_data_path = self.stock_data_dir / f"{symbol.upper()}.csv"
        if not us_data_path.exists():
            us_data_path = self.reference_data_dir / f"{symbol.upper()}.csv"
        if us_data_path.exists():
            df = self._load_data_file(us_data_path)
        else:
            self.cache.set(cache_key, None)
            return None

        if df is None or df.empty:
            # Cache the None result
            self.cache.set(cache_key, None)
            return None

        # Ensure date column is datetime
        df['date'] = pd.to_datetime(df['date'])

        # Sort by date in descending order (most recent first)
        df = df.sort_values('date', ascending=False)

        # Convert all data to StockData objects (for caching)
        all_stock_data = []
        for _, row in df.iterrows():
            all_stock_data.append(StockData(
                symbol=symbol,
                date=row['date'],
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=int(row['volume'])
            ))

        # Cache all data
        full_result = StockDataResponse(
            symbol=symbol,
            data=all_stock_data,
            total_records=len(all_stock_data)
        )
        self.cache.set(cache_key, full_result)
        logger.debug(f"Cached all stock data: {symbol}")

        # Apply filters and return
        return self._apply_data_filters(full_result, start_datetime, end_datetime, period_days, period)

    async def get_all_stocks(self) -> List[str]:
        """Every symbol currently in a tracked index, sorted.

        Backs the symbol list and the search box, so it has to agree with what
        the rest of the backend will actually serve: offering a name in search
        that the screener has dropped and the chart cannot load is worse than
        not offering it. Membership comes from the one place that defines it.
        """
        cache_key = CacheKeyBuilder.all_stocks()
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            logger.debug("Retrieved all stocks from cache")
            return cached_result

        result = universe_service.get_universe()
        self.cache.set(cache_key, result)
        logger.debug(f"Cached {len(result)} stocks from current index membership")

        return result

    async def get_all_trading_calendar(self) -> List[str]:
        """Get all trading calendar dates"""
        cache_key = CacheKeyBuilder.trading_calendar()
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            logger.debug("Retrieved trading calendar from cache")
            return cached_result

        all_dates = []
        if self.us_calendar_path.exists():
            try:
                # Use pandas to read the file directly
                df = pd.read_csv(self.us_calendar_path, header=None, names=['date'], dtype=str)
                df = df.dropna()
                df['date'] = df['date'].str.strip()
                all_dates = sorted(df['date'].tolist(), reverse=True)  # Most recent first
                logger.debug(f"Read {len(all_dates)} trading dates from {self.us_calendar_path}")
            except Exception as e:
                logger.error(f"Failed to read US calendar file {self.us_calendar_path}: {e}")
        else:
            logger.warning(f"Trading calendar file not found: {self.us_calendar_path}")

        # Cache the result for 1 hour (trading calendar doesn't change frequently)
        self.cache.set(cache_key, all_dates, ttl_minutes=60)
        logger.debug("Cached trading calendar")
        return all_dates


    async def get_trading_calendar(self, limit: int = 100, offset: int = 0) -> TradingCalendarResponse:
        """Get trading calendar dates from us_calendar_path with pagination

        Args:
            limit: Maximum number of dates to return (default: 100)
            offset: Number of dates to skip (default: 0)

        Returns:
            TradingCalendarResponse object
        """
        all_dates = await self.get_all_trading_calendar()
        total_count = len(all_dates)
        end_index = offset + limit
        paginated_dates = all_dates[offset:end_index]

        logger.debug(
            f"Returning {len(paginated_dates)} trading dates (offset: {offset}, limit: {limit}, total: {total_count})"
        )

        return TradingCalendarResponse(
            dates=paginated_dates,
            total_count=total_count,
            limit=limit,
            offset=offset
        )

    async def is_trading_day(self, date: str) -> bool:
        """Check if a given date is a trading day"""
        try:
            # Validate date format
            datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            raise ValueError("Invalid date format. Use YYYY-MM-DD")

        calendar_dates = await self.get_all_trading_calendar()
        return date in calendar_dates

    async def get_stock_picks_history(self, symbol: str, start_date: datetime, end_date: datetime) -> List[BacktestSignal]:
        """Get stock picks history for backtest"""
        # Check cache first
        start_date_str = start_date.strftime('%Y-%m-%d')
        end_date_str = end_date.strftime('%Y-%m-%d')
        cache_key = CacheKeyBuilder.picks_history(symbol, start_date_str, end_date_str)
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Retrieved picks history from cache: {symbol} ({start_date_str} to {end_date_str})")
            return cached_result

        # Get picks from MongoDB
        picks_data = await picks_service.get_picks_by_symbol(symbol, start_date, end_date)

        picks_history = []
        for pick_data in picks_data:
            try:
                file_date = datetime.strptime(pick_data["date"], '%Y-%m-%d')
                direction = Direction(pick_data["direction"])

                picks_history.append(BacktestSignal(
                    date=file_date,
                    direction=direction,
                    score=pick_data.get("score"),
                    strategy=pick_data.get("strategy")
                ))
            except (ValueError, KeyError) as e:
                logger.warning(f"Invalid pick data for {symbol}: {e}")
                continue

        result = sorted(picks_history, key=lambda x: x.date)

        # Cache the result
        self.cache.set(cache_key, result)
        logger.debug(f"Cached picks history: {symbol} ({start_date_str} to {end_date_str})")

        return result

    async def calculate_backtest_performance(self, symbol: str, picks_history: List[BacktestSignal]) -> List[BacktestPerformance]:
        """Calculate backtest performance"""
        # Get stock data (use full data set for performance calculation)
        stock_response = await self.get_stock_data(symbol, period=None)
        if not stock_response:
            return []

        # Get trading calendar for calculating trading days
        trading_calendar = await self.get_all_trading_calendar()
        trading_dates = set(trading_calendar)

        # Convert to DataFrame for easier processing
        stock_data = pd.DataFrame([{
            'date': data.date,
            'close': data.close
        } for data in stock_response.data])
        stock_data['date'] = pd.to_datetime(stock_data['date'])

        performance_data = []

        for pick in picks_history:
            pick_date = pick.date
            pick_date_str = pick_date.strftime('%Y-%m-%d')

            # Find signal day price
            signal_day_data = stock_data[stock_data['date'].dt.date == pick_date.date()]

            if signal_day_data.empty:
                continue

            signal_price = signal_day_data.iloc[0]['close']

            # Calculate returns for 1, 3, 5, 10 trading days
            for n_trading_days in [1, 3, 5, 10]:
                # Find the nth trading day after the signal date
                future_trading_date = self._get_nth_trading_day_after(pick_date_str, n_trading_days, trading_dates)

                if future_trading_date:
                    # Convert back to datetime for stock data lookup
                    future_date = datetime.strptime(future_trading_date, '%Y-%m-%d')
                    future_data = stock_data[stock_data['date'].dt.date == future_date.date()]

                    if not future_data.empty:
                        future_price = future_data.iloc[0]['close']
                        return_pct = (future_price - signal_price) / signal_price * 100

                        performance_data.append(BacktestPerformance(
                            signal_date=pick_date_str,
                            direction=pick.direction,
                            days=n_trading_days,
                            return_pct=return_pct,
                            score=pick.score
                        ))

        return performance_data

    async def run_backtest(self, symbol: str, period_days: int, user_role: str, premium_expires_at: Optional[datetime] = None) -> BacktestResult:
        """Run backtest for a symbol"""
        # Check cache first
        cache_key = CacheKeyBuilder.backtest_result(symbol, period_days, user_role, premium_expires_at)
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Retrieved backtest result from cache: {symbol}, {period_days} days")
            return cached_result

        # Calculate time range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=period_days)

        # For non-premium users, restrict the end date to respect the free user limit
        if not can_access_recent_data(user_role, premium_expires_at, end_date):
            # Adjust end_date to the latest date that free users can access
            limit_date = datetime.now(timezone.utc) - timedelta(days=settings.free_user_days_limit)
            if end_date > limit_date:
                end_date = limit_date
                logger.info(f"Adjusted backtest end date for non-premium user: {end_date.strftime('%Y-%m-%d')}")

            # Recalculate start_date based on adjusted end_date
            start_date = end_date - timedelta(days=period_days)

        # Get picks history
        picks_history = await self.get_stock_picks_history(symbol, start_date, end_date)

        # Calculate performance
        performance = await self.calculate_backtest_performance(symbol, picks_history)

        # Calculate summary statistics
        summary_stats = {}
        if performance:
            perf_df = pd.DataFrame([{
                'direction': p.direction.value,
                'days': p.days,
                'return_pct': p.return_pct
            } for p in performance])

            # Group by direction and days
            for direction in ['rising', 'declining']:
                summary_stats[direction] = {}
                for days in [1, 3, 5, 10]:
                    subset = perf_df[
                        (perf_df['direction'] == direction) &
                        (perf_df['days'] == days)
                    ]

                    if not subset.empty:
                        # For declining direction, invert the return_pct to get actual profit
                        # (negative price movement is positive profit for short positions)
                        actual_returns = subset['return_pct']
                        if direction == 'declining':
                            actual_returns = -actual_returns

                        summary_stats[direction][f'{days}d'] = {
                            'avg_return': float(actual_returns.mean()),
                            'win_rate': float((actual_returns > 0).mean() * 100),
                            'max_return': float(actual_returns.max()),
                            'min_return': float(actual_returns.min()),
                            'count': len(subset)
                        }

        result = BacktestResult(
            symbol=symbol,
            period_days=period_days,
            signals=picks_history,
            performance=summary_stats
        )

        # Cache the result
        self.cache.set(cache_key, result)
        logger.debug(f"Cached backtest result: {symbol}, {period_days} days")

        return result

    # Cache management methods
    def clear_cache(self) -> None:
        """Clear all cache entries"""
        self.cache.clear()
        logger.info("All cache entries cleared")

    def cleanup_expired_cache(self) -> int:
        """Clean up expired cache entries and return count of removed entries"""
        return self.cache.cleanup_expired()

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return self.cache.get_stats()

    def invalidate_cache_for_date(self, date: str) -> None:
        """Invalidate all cache entries for a specific date"""
        keys_to_delete = []

        # Daily picks cache keys
        for direction in ['rising', 'falling']:
            key = CacheKeyBuilder.daily_picks(date, direction)
            if self.cache.delete(key):
                keys_to_delete.append(key)

        # Predictions cache key
        key = CacheKeyBuilder.predictions(date)
        if self.cache.delete(key):
            keys_to_delete.append(key)

        # Available dates cache (since it might be affected by new data)
        key = CacheKeyBuilder.available_dates()
        if self.cache.delete(key):
            keys_to_delete.append(key)

        if keys_to_delete:
            logger.info(f"Invalidated cache for date {date}: {len(keys_to_delete)} entries")

    def invalidate_stock_cache(self, symbol: str) -> None:
        """Invalidate all cache entries for a specific stock symbol"""
        keys_to_delete = []

        # Stock data cache
        key = CacheKeyBuilder.stock_data(symbol)
        if self.cache.delete(key):
            keys_to_delete.append(key)

        # Note: We don't delete picks_history cache here as it would require
        # checking all possible date ranges, which is impractical

        if keys_to_delete:
            logger.info(f"Invalidated cache for stock {symbol}: {len(keys_to_delete)} entries")

    def warm_cache_for_recent_dates(self, days: int = 7) -> None:
        """Warm cache by pre-loading data for recent dates"""
        from datetime import date, timedelta

        logger.info(f"Starting cache warm-up for last {days} days")

        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        # Warm up available dates
        try:
            import asyncio
            asyncio.create_task(self.get_available_dates())
        except Exception as e:
            logger.error(f"Failed to warm cache for available dates: {e}")

# Create global stock service instance
stock_service = StockService()
