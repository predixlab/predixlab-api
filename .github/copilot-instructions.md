# GitHub Copilot Instructions

## Project Overview

This is a FastAPI-based backend for PredixLab, a stock prediction and analysis platform. The project provides:

- Email verification and OAuth (Google, Microsoft) authentication
- Stock prediction and daily signals APIs
- Premium membership system with Stripe integration
- Historical stock data and backtest analysis

## Tech Stack

- **Framework**: FastAPI (Python 3.12+)
- **Database**: MongoDB with the PyMongo async client
- **Authentication**: JWT tokens, OAuth 2.0 (AuthLib)
- **Email**: Azure Communication Services
- **Payment**: Stripe
- **Task Scheduling**: APScheduler
- **Data Processing**: Pandas, NumPy
- **Validation**: Pydantic v2 models
- **Testing**: pytest with pytest-asyncio

## Project Structure

```
├── app/
│   ├── main.py           # FastAPI application entry point
│   ├── config.py         # Application settings (Pydantic Settings)
│   ├── database.py       # MongoDB connection management
│   ├── core/             # Core utilities (security, dependencies, i18n, caching)
│   ├── models/           # Pydantic models for data validation
│   ├── services/         # Business logic layer
│   ├── routers/          # API route handlers
│   ├── jobs/             # Background scheduled jobs
│   ├── templates/        # Email templates (Jinja2)
│   └── translations/     # i18n translation files
├── tests/                # Test suite organized by layer
├── pyproject.toml        # Project dependencies and pytest config
└── uv.lock               # Dependency lock file
```

## Development Guidelines

### Code Style

- Follow PEP 8 style guidelines
- Use type hints for all function parameters and return values
- Use async/await for all I/O operations
- Use Pydantic models for request/response validation
- Keep services stateless; use dependency injection

### API Development

- All API routes should be in `app/routers/`
- Use FastAPI dependency injection for authentication and database access
- Return appropriate HTTP status codes
- Use Pydantic models for all request/response bodies
- Follow RESTful API conventions

### Testing

- Write tests for all new features and bug fixes
- Place tests in `tests/` directory, mirroring the app structure
- Use pytest fixtures from `tests/conftest.py` for common test data
- Mock external services (MongoDB, email, Stripe) in tests
- Run tests with: `python -m pytest tests/ -v`

### Environment Variables

- Use `.env.example` as a template for required environment variables
- Access settings through `app.config.settings`
- Never commit sensitive values to the repository

### Database Operations

- Use async MongoDB operations via the PyMongo async client
- Access database through dependency injection
- Use proper indexing for frequently queried fields

### Public Documentation

- Keep repository documentation in English and public examples free of credentials and personal identifiers.
- Follow `CONTRIBUTING.md` for validation and `SECURITY.md` for private vulnerability reporting.
- Keep JWT and OAuth session signing keys separate; `SESSION_SECRET_KEY` is required and must not be hardcoded.

### Authentication

- JWT tokens for API authentication
- OAuth 2.0 for Google and Microsoft login
- Email verification codes for passwordless login
- Role-based access control (free, premium, admin)
