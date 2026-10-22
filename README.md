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

- The hosted data-collection job (Collector) produces market data, index constituents, and the read-only snapshots below `snapshots/us/`. It isn't in the public repositories.
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
- PyJWT for JWT signing and verification; existing HS256 tokens remain valid
  with the same signing key, claims, and expiration semantics
- MongoDB (PyMongo async client)
- Pandas, NumPy, PyArrow
- APScheduler
- pytest and pytest-asyncio
- Locked dependencies in `uv.lock`; [uv](https://docs.astral.sh/uv/) is recommended

Security-related transitive dependency minimums are recorded in
`[tool.uv].constraint-dependencies` in `pyproject.toml` and enforced when
regenerating `uv.lock`.

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
- set `DEBUG=True` to enable OpenAPI, Swagger UI, and ReDoc unless `ENABLE_API_DOCS` overrides it.

Generate a different value for each signing key:

```bash
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Provide data

Identity, business routes, and Market Overview use MongoDB. Collector publishes one compact document per trading day to the `market_overviews` collection. File-backed stock and screener routes also need:

```text
data/
├── stock_data/us_data/        # Per-symbol OHLCV CSV files
├── reference_data/us_indices/ # Configured reference/index OHLCV CSV files
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

When `ENABLE_API_DOCS=True` (or unset with `DEBUG=True`):

- OpenAPI schema: <http://localhost:8000/openapi.json>
- Swagger UI: <http://localhost:8000/docs>
- ReDoc: <http://localhost:8000/redoc>

Production mode disables all three endpoints by default. Every response includes
`X-Robots-Tag: noindex, nofollow`, and `/robots.txt` disallows crawling the entire API.

### Directional signal labels

Pivot `action` responses use `STRONGLY_BULLISH`, `BULLISH`, `NEUTRAL`,
`BEARISH`, and `STRONGLY_BEARISH`. Technical analysis `signal` responses use
the lowercase equivalents. Legacy Pivot states (`STRONG_BUY`, `BUY`, `HOLD`,
`SELL`, `STRONG_SELL`) still published to MongoDB by the Signals pipeline are
normalized when parsed, including pick details, summaries, histories, and the
combined Signals board. Legacy technical-analysis values are also accepted on
model input; responses and OpenAPI enums always use the new directional labels.

There are no action-value query parameters or MongoDB action filters in the
API. Existing `direction` (`long`/`short`) and `event` (`BUY_TURN`, `SELL_TURN`,
`TREND_BUY`, `BASE_BREAKOUT`) filters are unchanged and select documents with
either legacy or new actions. Clients displaying action labels must recognize
the new response values; no stored-data migration is required.

## Configuration

`Settings` in `app/config.py` reads environment variables or the repository-root `.env`. See the complete copyable template in [`.env.example`](.env.example).

### Core and identity

| Variable | Default | Purpose |
| --- | --- | --- |
| `MONGODB_URL` | `mongodb://localhost:27017` | MongoDB connection URI |
| `DATABASE_NAME` | `predixlab` | Database name |
| `EXPIRY_CLEANUP_INTERVAL_SECONDS` | `3600` | Interval between expired verification-code, rate-limit, session, login-code, and refresh-token purges; must be positive |
| `SECRET_KEY` | insecure placeholder | Signs JWTs and legacy unsubscribe links; fallback for new links; replace before deployment |
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
| `DEBUG` | `False` | Debug logging and reload for `start.py` / direct `app.main` execution, and API docs when `ENABLE_API_DOCS` is unset |
| `ENABLE_API_DOCS` | unset | Overrides `DEBUG` for `/openapi.json`, `/docs`, and `/redoc`; accepts `True` or `False` |
| `CORS_ORIGINS` | local `3000`, `8080` | Allowed origins as a JSON array |
| `PUBLIC_API_URL` | `http://localhost:8000` | API base URL for email unsubscribe links |
| `EMAIL_BACKEND` | `acs` | `acs` sends email; `console` writes messages to the application log |
| `UNSUBSCRIBE_SECRET` | empty (uses `SECRET_KEY`) | Dedicated unsubscribe signing key; use the same value in API and Campaign |
| `UNSUBSCRIBE_ALLOW_LEGACY_TOKENS` | `True` | Accept untimed legacy links and timed links verified only by the `SECRET_KEY` fallback |
| `UNSUBSCRIBE_LEGACY_CUTOFF` | unset | ISO date/datetime at which legacy links stop being accepted; date-only and timezone-less values mean UTC |
| `ACS_CONNECTION_STRING` / `ACS_SENDER` | empty | Azure Communication Services email configuration |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | empty | Google OAuth |
| `GOOGLE_OAUTH_CALLBACK_URL` | local API callback | Google server callback |
| `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` | empty | Microsoft OAuth |
| `MICROSOFT_OAUTH_CALLBACK_URL` | local API callback | Microsoft server callback |
| `OAUTH_REDIRECT_URL` | `http://localhost:3000/auth/callback` | Frontend URL after OAuth completes |

New marketing and Daily Signals unsubscribe links use timestamped signatures and
expire after 365 days. By default, verification accepts timed links signed with
either `SECRET_KEY` or `UNSUBSCRIBE_SECRET`, and existing untimed links signed with
`SECRET_KEY` remain valid. Set `UNSUBSCRIBE_ALLOW_LEGACY_TOKENS=False`, or set
`UNSUBSCRIBE_LEGACY_CUTOFF` (for example, `2027-01-01T00:00:00Z`) and wait until
that instant, to reject both untimed links and links verified only by the
`SECRET_KEY` fallback. The cutoff applies when `now >= cutoff`, regardless of a
token's issue date. Timed links signed with `UNSUBSCRIBE_SECRET` are unaffected;
expired timed links are always rejected.

For migration, first configure the same dedicated `UNSUBSCRIBE_SECRET` in the API
and Campaign Function App, and have Campaign issue timed links using that key.
Once all outstanding untimed links have aged out (approximately 60-90 days after
Campaign starts issuing those links), set the cutoff or disable legacy tokens in
the API. Then remove `SECRET_KEY` from the Campaign Function App. Keep `SECRET_KEY`
in the API for JWT signing; the default settings preserve existing link behavior
until legacy acceptance is explicitly retired.

### Session key deployment and rotation

The API refuses to start if `SESSION_SECRET_KEY` is missing, blank, or shorter than 32 characters excluding surrounding whitespace. Use a generated high-entropy value, keep it separate from `SECRET_KEY`, and supply it through the environment or a secret manager. All API replicas must share the same stable value; do not generate a new value on every startup.

Before rolling out this change, configure a new session key in every environment. Replacing the session key invalidates all existing session cookies, including cookies signed with the retired hardcoded key. Complete the rollout across all replicas; in-progress OAuth sign-ins must restart. There is no fallback to the old key.

### Email verification-code protection

Six-digit email codes expire after 10 minutes, become unusable after five failed
attempts, and are consumed atomically on success. A replacement code resets the
failure count. Login and registration share MongoDB-backed fixed-window counters
in `rate_limits`, with an `expires_at` TTL index created at startup.
Expiry checks do not depend on TTL deletion: verification and refresh lookups
require `expires_at > now`, counters use time buckets and an expiry predicate,
and resend cooldowns distinguish expired entries explicitly. `user_sessions`
currently has no session-validation read path.

A background cleanup starts immediately after application initialization, then
runs every `EXPIRY_CLEANUP_INTERVAL_SECONDS`. Each pass deletes documents with
`expires_at < now` from `verification_codes`, `rate_limits`, `user_sessions`,
`login_codes`, and `refresh_tokens`. Documents at or after the expiry boundary,
or without an expiry field, are preserved.
This bounds expired-document storage on Cosmos DB MongoDB RU deployments, where
TTL on custom fields may not be enforced. The deletes are idempotent across
replicas; errors are logged, other collections and later passes continue, and
the task is cancelled before MongoDB closes. Existing TTL indexes remain a
best-effort optimization; index-creation failures are logged and non-fatal.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OTP_REQUEST_EMAIL_LIMIT` / `OTP_REQUEST_IP_LIMIT` | `5` / `20` | Code requests per email / client IP per window |
| `OTP_REQUEST_WINDOW_SECONDS` | `3600` | Code-request window duration |
| `OTP_VERIFY_EMAIL_LIMIT` / `OTP_VERIFY_IP_LIMIT` | `10` / `30` | Verification requests per email / client IP per window |
| `OTP_VERIFY_WINDOW_SECONDS` | `900` | Verification window duration |
| `OTP_RESEND_COOLDOWN_SECONDS` | `60` | Minimum delay between code requests for the same email |
| `OAUTH_EXCHANGE_IP_LIMIT` / `OAUTH_EXCHANGE_WINDOW_SECONDS` | `20` / `300` | OAuth code exchanges per client IP / fixed-window duration |
| `REFRESH_IP_LIMIT` / `REFRESH_WINDOW_SECONDS` | `60` / `300` | Token refreshes per client IP / fixed-window duration |
| `TRUSTED_PROXY_HOPS` | `1` | Select the client address this many hops from the right of `X-Forwarded-For`; `0` disables this header |
| `TRUST_X_REAL_IP` | `False` | Explicitly allow `X-Real-IP` as a fallback before the socket address |

All limits and durations must be positive. Requests, including rejected attempts,
count against both dimensions; email keys are case-insensitive. Throttled requests
return a generic HTTP 429 response with `Retry-After` in seconds. Code-request
responses do not disclose whether an account exists, and cooldowns also apply to
ineligible addresses. Database errors fail closed rather than bypassing limits.

The default proxy setting matches Azure Container Apps ingress, which appends the
real client IP on the right. Only trust forwarded headers when every request
passes through ingress that supplies those hops; use `TRUSTED_PROXY_HOPS=0` for
direct access. Invalid or incomplete proxy chains fall back to the socket address
(or a valid `X-Real-IP` when explicitly enabled).

OAuth code exchange and token refresh use separate MongoDB-backed per-IP
fixed-window limits before any token processing. Rejected attempts count, and
HTTP 429 responses include `Retry-After`.

### Files and external services

| Variable | Default | Purpose |
| --- | --- | --- |
| `MARKET_OVERVIEWS_COLLECTION` | `market_overviews` | Compact daily Market Overview documents |
| `PREDIXLAB_DATA_DIR` | unset | Shared data root; screener snapshot is below `snapshots/us/` |
| `PREDICTIONS_DIR` / `PICKS_DIR` | `data/...` | Compatibility fields retained in `Settings`; the service has no read point |
| `US_STOCK_DATA_DIR` | `data/stock_data/us_data` | OHLCV CSV directory for symbols outside `US_REFERENCE_SYMBOLS` |
| `US_REFERENCE_SYMBOLS` | `SPY,QQQ,IWM,DIA` | Comma-separated reference/index symbols; case-insensitive, with whitespace trimmed and empty entries ignored |
| `US_REFERENCE_DATA_DIR` | derived | Optional OHLCV CSV directory used only for symbols in `US_REFERENCE_SYMBOLS` |
| `US_INDEX_PATH` | `data/instruments/us.txt` | US instrument universe |
| `US_CALENDAR_PATH` | `data/calendar/us.txt` | US trading calendar |
| `STRIPE_SECRET_KEY` | empty | Stripe server secret |
| `STRIPE_PUBLISHABLE_KEY` | empty | Stripe client publishable key |
| `STRIPE_WEBHOOK_SECRET` | empty | Stripe webhook signing secret |
| `STRIPE_MONTHLY_PRICE_ID` / `STRIPE_YEARLY_PRICE_ID` | empty | Monthly/yearly Price IDs |
| `STRIPE_TRIAL_PERIOD_DAYS` | `30` | Trial days for a first subscription |

When `PREDIXLAB_DATA_DIR` is unset, the snapshot root derives from `US_STOCK_DATA_DIR`; with defaults, the API reads `data/snapshots/us/`.

The API classifies each symbol using `US_REFERENCE_SYMBOLS` before choosing its history directory. Configured symbols read only from the reference/index directory; all others read only from `US_STOCK_DATA_DIR`. There is no cross-directory fallback, even if a CSV is missing or exists in both directories. `^GSPC` is not in the default list and continues to use `US_STOCK_DATA_DIR`.

Reference history uses `US_REFERENCE_DATA_DIR` when set; otherwise it uses `<PREDIXLAB_DATA_DIR>/reference_data/us_indices`. If neither variable is set, it derives the data root from `Path(US_STOCK_DATA_DIR).parents[1]`. For example, `/data/stock_data/us_data` resolves to `/data/reference_data/us_indices`, with no additional environment configuration required.

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

`GET /api/v1/stocks/data/{symbol}` includes the required boolean `is_index`, classified case-insensitively from `US_REFERENCE_SYMBOLS`. Clients should use only this flag to identify index symbols; it is included in cached and filtered responses.

When enabled, the runtime OpenAPI specification is authoritative for request parameters and response models; route implementations live in `app/routers/`. See the [Stripe integration guide](STRIPE_INTEGRATION.md) for Stripe events, authentication boundaries, and local testing.

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
- Runtime documentation when API docs are enabled: [`/docs`](http://localhost:8000/docs) and [`/redoc`](http://localhost:8000/redoc)
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
