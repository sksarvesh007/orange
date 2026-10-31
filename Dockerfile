# Multi-stage production Dockerfile for Errand Agent
# Stage 1: Dependency builder
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip uv \
    && uv pip install --no-cache-dir .

# Stage 2: Final minimal runtime
FROM python:3.12-slim AS runtime

WORKDIR /app

# Create non-root system user and group
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -s /bin/bash -m appuser

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Copy source code and migrations
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser alembic/ ./alembic/
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser pyproject.toml README.md ./

# Install local package into venv
RUN pip install --no-deps -e .

# Create artifacts directory for traces and logs
RUN mkdir -p /app/artifacts/traces && chown -R appuser:appuser /app/artifacts

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

ENTRYPOINT ["uvicorn", "errand_agent.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
