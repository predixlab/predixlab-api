# Use Python 3.12 slim image to match the project requirements
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies and uv
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && pip install --no-cache-dir uv==0.13.1 \
    && rm -rf /var/lib/apt/lists/*

# Copy uv configuration files
COPY pyproject.toml uv.lock README.md LICENSE ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Copy application code
COPY app/ ./app/
COPY main.py start.py ./

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD .venv/bin/python -c "import requests; requests.get('http://localhost:8000/health')"

# Run the application using uv
CMD ["uv", "run", "--frozen", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
