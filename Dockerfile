# Production Dockerfile for Google Cloud Run & GCP Deployment
FROM python:3.12-slim-bookworm

# Install runtime system packages (unixodbc for pyodbc, curl for healthchecks)
RUN apt-get update \
    && apt-get install -y --no-install-recommends unixodbc curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Set environment variables for production execution and Cloud Run port binding
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PORT=8080

# Install Python requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt typer

# Copy application directories and assets
COPY api/ ./api/
COPY etl_framework/ ./etl_framework/
COPY frontend/ ./frontend/
COPY pyproject.toml ./

# Default Cloud Run port
EXPOSE 8080

# Health check probe against the FastAPI health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# Launch uvicorn dynamically binding to Google Cloud Run's $PORT environment variable
CMD ["sh", "-c", "exec python -m uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
