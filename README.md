# PredixLab API

[Contributing](CONTRIBUTING.md) | [Security](SECURITY.md) | [Apache-2.0](LICENSE)

PredixLab API is the FastAPI backend for PredixLab. It provides Dashboard and other clients with a consistent HTTP API for identity, accounts, market data, picks, analysis, watchlists, and subscriptions. It combines business data in MongoDB with read-only file snapshots produced by Collector.

This repository is responsible for **reading, authentication, and service orchestration**. It does not train prediction models or generate daily market or screener snapshots.

## Role in PredixLab

```text
Dashboard / API clients
          │ HTTP + Bearer token
          ▼
     PredixLab API
       ├── MongoDB: users, sessions, picks, predictions, options, Pivot, subscriptions
       ├── PredixLab Collector: OHLCV, index constituents, market/screener snapshots
       ├── Pivot publisher: writes pivot_picks; pivot_runs provides run metadata
       ├── Azure Communication Services: transactional email
       └── Stripe: checkout, subscriptions, and webhooks
```

- `predixlab-collector` produces market data, index constituents, and the read-only snapshots below `snapshots/us/`.
- `predixlab-signals` can publish Pivot events to `pivot_picks`; the API run-metadata endpoint reads the separate `pivot_runs` collection.
- At startup, the API connects to MongoDB and loads its services. Between `02:00` and `02:29 UTC`, it checks every minute whether the latest-picks cache needs invalidation.

> **Pivot integration boundary:** this workspace's `predixlab-signals` daily job writes job status to `JOBS_COLLECTION` (normally `jobs`), while `GET /api/v1/pivot/runs/latest` reads `pivot_runs`. Publishing Signals events alone does not populate the run-metadata endpoint. Deployments that need it must synchronize or bridge the two storage contracts.

## Capabilities

| Area | Capability |
| --- | --- |
| Identity and accounts | Email-code registration and sign-in, Google/Microsoft OAuth, JWT access and refresh tokens, profile and account deletion, user-notice acceptance |
| Referrals | Unique invite links, verified signup attribution, premium-day rewards, conversion metrics, and abuse controls |
| Equities and model results | Available dates, daily picks, predictions, price history, symbol search, backtests |
| Pivot | Daily picks, symbol history, summaries, performance, and run metadata |
| Market intelligence | Market snapshots, breadth, sentiment, movers, return distributions, heat maps, and batch quotes |
| Symbol analysis | Technical and risk indicators, peers, correlations, seasonality, and profiles |
| Discovery and tracking | Custom and preset screeners, saved screens, watchlists, price and technical alerts |
| Options | Unusual-options activity queries |
| Membership | Free/premium visibility and Stripe monthly/yearly subscriptions |

Restricted picks, predictions, Pivot, unusual-options, and backtest queries hide the latest `10` days from free users. Premium users and administrators can access current data. Configure the window with `FREE_USER_DAYS_LIMIT`.

## Technology baseline

- Python `>=3.12`
- FastAPI, Pydantic v2, Uvicorn
- MongoDB (PyMongo async client)
- Pandas, NumPy, PyArrow
- APScheduler
- pytest and pytest-asyncio
- Locked dependencies in `uv.lock`; [uv](https://docs.astral.sh/uv/) is recommended

## Quick start

### 1. Install dependencies

Local development requires Python 3.12, uv, and an accessible MongoDB instance:

```bash
git clone https://github.com/predixlab/predixlab-api.git
cd predixlab-api
uv sync --frozen --extra test
cp .env.example .env
```

At minimum, in `.env`:

- replace `SECRET_KEY` with a strong random value;
- set `SESSION_SECRET_KEY` to a separate strong random value for OAuth session cookies;
- verify `MONGODB_URL` and `DATABASE_NAME`;
- use `EMAIL_BACKEND=console` locally when ACS is unavailable;
- set `DEBUG=True` to enable Swagger UI and ReDoc.

Generate a different value for each signing key:

```bash
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Provide data

Identity, business routes, and Market Overview use MongoDB. Collector publishes one compact document per trading day to the `market_overviews` collection. File-backed stock and screener routes also need:

```text
data/
├── stock_data/us_data/       # Per-symbol OHLCV CSV files
├── instruments/us.txt        # US instrument universe
├── calendar/us.txt           # Trading calendar
└── snapshots/us/
    └── screener.parquet      # Produced by Collector
```

The service can start without a Market Overview document or screener snapshot, but their endpoints return empty results; the API does not rebuild either artifact. Startup fails when MongoDB is unavailable.

### 3. Run and check

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/info
```

When `DEBUG=True`:

- Swagger UI: <http://localhost:8000/docs>
- ReDoc: <http://localhost:8000/redoc>

Production mode disables both endpoints by default.

## Configuration

`Settings` in `app/config.py` reads environment variables or the repository-root `.env`. See the complete copyable template in [`.env.example`](.env.example).

### Core and identity

| Variable | Default | Purpose |
| --- | --- | --- |
| `MONGODB_URL` | `mongodb://localhost:27017` | MongoDB connection URI |
| `DATABASE_NAME` | `predixlab` | Database name |
| `SECRET_KEY` | insecure placeholder | Signs JWTs and unsubscribe links; replace before deployment |
| `SESSION_SECRET_KEY` | required, no default | Independently signs OAuth session cookies; at least 32 characters excluding surrounding whitespace |
| `ALGORITHM` | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access-token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh-token lifetime |
| `FREE_USER_DAYS_LIMIT` | `10` | Free-user data-delay window |
| `USER_NOTICE_VERSION` | `2026-08-09` | User-notice version accepted during registration/confirmation |
| `REFERRAL_PROGRAM_ENABLED` | `True` | Enables referral attribution and rewards |
| `REFERRAL_FRONTEND_URL` | local Dashboard registration URL | Base URL for generated invite links |
| `REFERRAL_INVITER_REWARD_DAYS` / `REFERRAL_INVITEE_REWARD_DAYS` | `15` / `7` | Premium access granted after verified registration |
| `REFERRAL_MAX_REWARDED_REFERRALS` | `20` | Rewarded referral cap per inviter |

### Application, email, and OAuth

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_NAME` / `APP_VERSION` | `PredixLab` / `1.0.0` | Application information in OpenAPI and status responses |
| `DEBUG` | `False` | Debug logging, reload for direct `app.main` execution, and API docs |
| `CORS_ORIGINS` | local `3000`, `8080` | Allowed origins as a JSON array |
| `PUBLIC_API_URL` | `http://localhost:8000` | API base URL for email unsubscribe links |
| `EMAIL_BACKEND` | `acs` | `acs` sends email; `console` writes messages to the application log |
| `ACS_CONNECTION_STRING` / `ACS_SENDER` | empty | Azure Communication Services email configuration |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | empty | Google OAuth |
| `GOOGLE_OAUTH_CALLBACK_URL` | local API callback | Google server callback |
| `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` | empty | Microsoft OAuth |
| `MICROSOFT_OAUTH_CALLBACK_URL` | local API callback | Microsoft server callback |
| `OAUTH_REDIRECT_URL` | `http://localhost:3000/auth/callback` | Frontend URL after OAuth completes |

### Session key deployment and rotation

The API refuses to start if `SESSION_SECRET_KEY` is missing, blank, or shorter than 32 characters excluding surrounding whitespace. Use a generated high-entropy value, keep it separate from `SECRET_KEY`, and supply it through the environment or a secret manager. All API replicas must share the same stable value; do not generate a new value on every startup.

Before rolling out this change, configure a new session key in every environment. Replacing the session key invalidates all existing session cookies, including cookies signed with the retired hardcoded key. Complete the rollout across all replicas; in-progress OAuth sign-ins must restart. There is no fallback to the old key.

### Files and external services

| Variable | Default | Purpose |
| --- | --- | --- |
| `MARKET_OVERVIEWS_COLLECTION` | `market_overviews` | Compact daily Market Overview documents |
| `PREDIXLAB_DATA_DIR` | unset | Shared data root; screener snapshot is below `snapshots/us/` |
| `PREDICTIONS_DIR` / `PICKS_DIR` | `data/...` | Compatibility fields retained in `Settings`; the service has no read point |
| `US_STOCK_DATA_DIR` | `data/stock_data/us_data` | OHLCV CSV directory |
| `US_REFERENCE_DATA_DIR` | derived | Optional reference/index OHLCV CSV directory; used when a symbol has no CSV in `US_STOCK_DATA_DIR` |
| `US_INDEX_PATH` | `data/instruments/us.txt` | US instrument universe |
| `US_CALENDAR_PATH` | `data/calendar/us.txt` | US trading calendar |
| `STRIPE_SECRET_KEY` | empty | Stripe server secret |
| `STRIPE_PUBLISHABLE_KEY` | empty | Stripe client publishable key |
| `STRIPE_WEBHOOK_SECRET` | empty | Stripe webhook signing secret |
| `STRIPE_MONTHLY_PRICE_ID` / `STRIPE_YEARLY_PRICE_ID` | empty | Monthly/yearly Price IDs |
| `STRIPE_TRIAL_PERIOD_DAYS` | `30` | Trial days for a first subscription |

When `PREDIXLAB_DATA_DIR` is unset, the snapshot root derives from `US_STOCK_DATA_DIR`; with defaults, the API reads `data/snapshots/us/`.

Reference history (including SPY, QQQ, IWM, and DIA) uses `US_REFERENCE_DATA_DIR` when set; otherwise it uses `<PREDIXLAB_DATA_DIR>/reference_data/us_indices`. If neither variable is set, it derives the data root from `Path(US_STOCK_DATA_DIR).parents[1]`. For example, `/data/stock_data/us_data` resolves to `/data/reference_data/us_indices`, with no additional environment configuration required.

## API use

Business routes use the `/api/v1` prefix. Except explicitly public endpoints—registration, sign-in, OAuth callbacks, email unsubscribe, Stripe public configuration, and webhooks—routes generally require:

```http
Authorization: Bearer <access_token>
```

| Route | Description |
| --- | --- |
| `/health`, `/api/v1/info` | Health and capability information |
| `/api/v1/auth` | Registration, sign-in, OAuth, refresh, and sign-out |
| `/api/v1/user` | Profiles, notice acceptance, unsubscribe, and account deletion |
| `/api/v1/stocks` | Picks, predictions, prices, search, and backtests |
| `/api/v1/pivot` | Pivot picks, history, performance, and run information |
| `/api/v1/market` | Market snapshots, breadth, sentiment, movers, heat maps, and quotes |
| `/api/v1/analysis` | Technical, risk, peer, correlation, seasonality, and profile analysis |
| `/api/v1/screener` | Preset, custom, and saved screeners |
| `/api/v1/watchlist`, `/api/v1/alerts` | Watchlists and alerts |
| `/api/v1/options` | Unusual-options activity |
| `/api/v1/subscription` | Stripe prices, checkout, subscription state, cancellation, and webhooks |
| `/api/v1/referrals` | Public click attribution and authenticated referral performance |

The runtime OpenAPI specification is authoritative for request parameters and response models; route implementations live in `app/routers/`. See the [Stripe integration guide](STRIPE_INTEGRATION.md) for Stripe events, authentication boundaries, and local testing.

## Common commands

```bash
# Run the development service
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run the full CI-equivalent test suite
uv run python -m pytest tests/ -v --tb=short

# Run a focused test file
uv run python -m pytest tests/routers/test_subscription.py -v
```

Tests use mocks or temporary synthetic data and do not require live MongoDB, ACS, or Stripe.

## Deployment

`Dockerfile` uses Python 3.12, installs locked production dependencies, listens on `8000`, and uses `/health` for its container health check:

```bash
docker build -t predixlab-api .
```

Supply a MongoDB address reachable from the container and mount shared data at paths consistent with `PREDIXLAB_DATA_DIR`, `US_STOCK_DATA_DIR`, and related variables. The main-branch deployment workflow builds an image and publishes it to Azure Container Apps.

The current `docker-compose.yml` is **not a ready-to-run local solution**: it references a missing `mongo-init.js`, and its data volume does not cover the default `data/...` configuration. Do not use `docker compose up` as the standard startup command until those deployment parameters are corrected.

## Project structure

```text
app/
├── main.py          # Application entry point, middleware, lifecycle, scheduler
├── config.py        # Pydantic Settings
├── database.py      # MongoDB connection and indexes
├── routers/         # HTTP routes
├── services/        # Business and data access
├── models/          # Request, response, and persistence models
├── jobs/            # Scheduled jobs
├── core/            # Authentication, email, metrics, analysis, i18n
└── templates/       # Email templates
tests/               # pytest tests organized by layer
```

## Documentation

- [Environment template](.env.example)
- [Stripe integration guide](STRIPE_INTEGRATION.md)
- [Referral program](REFERRAL_PROGRAM.md)
- Runtime documentation in debug mode: [`/docs`](http://localhost:8000/docs) and [`/redoc`](http://localhost:8000/redoc)
- [Project development constraints](.github/copilot-instructions.md)

## Known boundaries

- Market data, snapshots, model results, and Pivot data are supplied by external jobs; this repository cannot generate complete production data itself.
- Market and screener services read only Collector snapshots and return empty data if files are missing or incompatible.
- Most business routes require a valid user and Bearer token.
- OAuth, ACS, and Stripe require provider-side callback, domain, and credential configuration.
- The repository has no standalone documentation builder or Markdown link checker.

## Contributing and support

Read [CONTRIBUTING.md](CONTRIBUTING.md) for development checks and pull requests.
Use [GitHub Issues](https://github.com/predixlab/predixlab-api/issues) for
reproducible bugs and feature proposals. Report vulnerabilities privately as
described in [SECURITY.md](SECURITY.md); never attach credentials or customer data.

## License

Copyright 2026 PredixLab contributors.

Project source is licensed under the [Apache License 2.0](LICENSE). Third-party
dependencies, data, models, and assets may have separate terms. The source
license does not provide access to hosted services or production infrastructure.
