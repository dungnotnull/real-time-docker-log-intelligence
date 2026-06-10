# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages
COPY --from=builder /install /usr/local

# Copy application code
COPY agent/ ./agent/
COPY tools/ ./tools/
COPY config/ ./config/
COPY SECOND-KNOWLEDGE-BRAIN.md .

# Create data and model directories
RUN mkdir -p /app/data /app/models

# Non-root user
RUN useradd -m -u 1000 agent && chown -R agent:agent /app
USER agent

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV HF_CACHE_DIR=/app/models
ENV DATA_DIR=/app/data

EXPOSE 8766

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8766/health || exit 1

CMD ["python", "agent/main.py", "monitor", "--host", "0.0.0.0", "--port", "8766"]
