from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse, Response
from contextlib import asynccontextmanager
import asyncio
import logging
import time
import os
from datetime import timezone
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, settings
from app.database import connect_to_mongo, close_mongo_connection
from app.core.maintenance import run_expiry_cleanup
from app.routers import auth, stocks, user, subscription, options, market, analysis, screener, watchlist, pivot, signals, referrals
from app.services.user_service import user_service
from app.services.stock_service import stock_service
from app.services.options_service import options_service
from app.services.picks_service import picks_service
from app.services.stripe_service import stripe_service
from app.services.predictions_service import predictions_service
from app.services.market_service import market_service
from app.services.analysis_service import analysis_service
from app.services.screener_service import screener_service
from app.services.screener_strategy_service import screener_strategy_service
from app.services.watchlist_service import watchlist_service
from app.services.alpharank_service import alpharank_service
from app.services.referral_service import referral_service
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.jobs.cache_monitor_jobs import monitor_latest_picks_cache as monitor_latest_picks_cache_job

X_ROBOTS_TAG = "noindex, nofollow"

# Configure logging
logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    app.state.background_tasks = []

    try:
        # Connect to MongoDB
        await connect_to_mongo()

        # Initialize services
        await user_service.initialize()
        await stock_service.initialize()
        await picks_service.initialize()
        await options_service.initialize()
        await stripe_service.initialize()
        await predictions_service.initialize()
        await market_service.initialize()
        await analysis_service.initialize()
        await screener_service.initialize()
        await screener_strategy_service.initialize()
        await watchlist_service.initialize()
        await alpharank_service.initialize()
        await referral_service.initialize()

        logger.info("Application startup completed successfully")
        # Setup APScheduler and schedule jobs
        scheduler = AsyncIOScheduler(timezone=timezone.utc)
        # Schedule monitor_latest_picks_cache to run during 02:00-02:29 UTC, once per minute at second 0
        scheduler.add_job(
            monitor_latest_picks_cache_job,
            CronTrigger(hour=2, minute="0-29", second=0, timezone=timezone.utc),
            id="monitor_latest_picks_cache",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=60,
            max_instances=1,
        )
        scheduler.start()
        app.state.scheduler = scheduler
        app.state.background_tasks.append(
            asyncio.create_task(run_expiry_cleanup(), name="expiry_cleanup")
        )
        logger.info("Started APScheduler with monitor_latest_picks_cache job (02:00-02:29 UTC)")

    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        raise

    try:
        yield
    finally:
        logger.info("Shutting down application...")
        try:
            scheduler = getattr(app.state, "scheduler", None)
            if scheduler:
                try:
                    scheduler.shutdown(wait=False)
                    logger.info("APScheduler shutdown initiated")
                except Exception:
                    logger.exception("Error shutting down APScheduler")
            for task in app.state.background_tasks:
                task.cancel()
            for task in app.state.background_tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("Error stopping background task")
            logger.info("Background tasks cancelled")
            await close_mongo_connection()
            logger.info("Application shutdown completed")
        except Exception:
            logger.exception("Error during shutdown")


def api_docs_kwargs(config: Settings) -> dict[str, str | None]:
    enabled = (
        config.enable_api_docs if config.enable_api_docs is not None else config.debug
    )
    return {
        "openapi_url": "/openapi.json" if enabled else None,
        "docs_url": "/docs" if enabled else None,
        "redoc_url": "/redoc" if enabled else None,
    }


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="PredixLab API - Stock prediction and analysis platform",
    **api_docs_kwargs(settings),
    lifespan=lifespan
)

# Add session middleware for request.session (required by authlib)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key.get_secret_value(),
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Add trusted host middleware for security
if not settings.debug:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "*.predixlab.com"]
    )

# Add request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response

# Add request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()

    # Log request
    logger.info(f"{request.method} {request.url.path} - {request.client.host if request.client else 'unknown'}")

    try:
        response = await call_next(request)
        process_time = time.time() - start_time

        # Log response
        logger.info(f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")

        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(f"{request.method} {request.url.path} - ERROR: {str(e)} - {process_time:.3f}s")
        raise

# Registered after CORS so preflight responses also receive the header.
@app.middleware("http")
async def add_robots_header(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = X_ROBOTS_TAG
    return response


# Exception handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "error": True,
            "message": exc.detail,
            "status_code": exc.status_code
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        # ServerErrorMiddleware handles exceptions outside HTTP middleware.
        headers={"X-Robots-Tag": X_ROBOTS_TAG},
        content={
            "error": True,
            "message": "Internal server error" if not settings.debug else str(exc),
            "status_code": 500
        }
    )

# Include routers
app.include_router(auth.router, prefix="/api/v1")
app.include_router(user.router, prefix="/api/v1")
app.include_router(stocks.router, prefix="/api/v1")
app.include_router(pivot.router, prefix="/api/v1")
app.include_router(signals.router, prefix="/api/v1")
app.include_router(subscription.router, prefix="/api/v1")
app.include_router(options.router, prefix="/api/v1")
app.include_router(market.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(screener.router, prefix="/api/v1")
app.include_router(watchlist.router, prefix="/api/v1")
app.include_router(watchlist.alerts_router, prefix="/api/v1")
app.include_router(referrals.router, prefix="/api/v1")

# Root endpoints
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint"""
    return {
        "message": f"Welcome to {settings.app_name}",
        "version": settings.app_version,
        "status": "operational",
        "docs_url": app.docs_url or "Contact admin for API documentation"
    }

@app.api_route(
    "/robots.txt",
    methods=["GET", "HEAD"],
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def robots_txt() -> PlainTextResponse:
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "app_name": settings.app_name,
        "version": settings.app_version,
        "timestamp": time.time()
    }

@app.get("/api/v1/info", tags=["API Info"])
async def api_info():
    """API information endpoint"""
    return {
        "app_name": settings.app_name,
        "version": settings.app_version,
        "description": "PredixLab API provides market intelligence, stock analysis, screening, watchlists, predictions and backtests",
        "features": [
            "Email verification login",
            "OAuth login (Google, Microsoft)",
            "Daily signals",
            "Stock prediction results",
            "Historical stock data",
            "Backtest analysis",
            "Market overview, breadth and sentiment",
            "Market movers and heatmaps",
            "Technical indicators with plain-language interpretation",
            "Risk and performance analytics",
            "Peer comparison, correlation and seasonality",
            "Multi-criteria stock screener with preset strategies",
            "Watchlists and price/technical alerts",
            "Unusual options activity",
            "Premium membership system"
        ],
        "endpoints": {
            "auth": "/api/v1/auth",
            "user": "/api/v1/user",
            "stocks": "/api/v1/stocks",
            "signals": "/api/v1/signals",
            "market": "/api/v1/market",
            "analysis": "/api/v1/analysis",
            "screener": "/api/v1/screener",
            "watchlist": "/api/v1/watchlist",
            "alerts": "/api/v1/alerts",
            "options": "/api/v1/options",
            "subscription": "/api/v1/subscription"
        }
    }

# Well-known endpoints
@app.get("/.well-known/microsoft-identity-association.json", tags=["Well-known"])
async def microsoft_identity_association():
    """Microsoft identity association endpoint for OAuth configuration"""
    file_path = os.path.join("app", "assets", "microsoft-identity-association.json")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Microsoft identity association file not found")

    return FileResponse(
        path=file_path,
        media_type="application/json",
        filename="microsoft-identity-association.json"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level="info"
    )
