# ============================================================
# ElevateBox Voice Agent — Dockerfile
# Multi-stage build: keeps the final image small and clean.
# ============================================================

# ---- Build stage -------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools for any C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Runtime stage -----------------------------------------
FROM python:3.11-slim AS runtime

# Non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY src/ ./src/
COPY static/ ./static/

# Ensure we never run as root in production
USER appuser

# Uvicorn binds to 0.0.0.0 so the container is reachable
# PORT env var comes from Render / the platform
ENV PORT=8000

EXPOSE $PORT

# Explicit healthcheck so orchestrators know when the app is ready
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')"

CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT}"]
