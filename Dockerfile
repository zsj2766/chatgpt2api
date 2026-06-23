ARG BUILDPLATFORM
ARG TARGETPLATFORM
ARG TARGETARCH

# ============================================
# Stage 1: Frontend Build (Node.js Alpine)
# ============================================
FROM --platform=$BUILDPLATFORM node:22-alpine AS web-build

WORKDIR /app/web

# Copy dependency manifests first (better cache)
COPY web/package.json web/bun.lock ./
RUN npm install --production=false

# Copy VERSION and CHANGELOG for build-time injection
COPY VERSION /app/VERSION
COPY CHANGELOG.md /app/CHANGELOG.md

# Copy source and build
COPY web ./
RUN NEXT_PUBLIC_APP_VERSION="$(cat /app/VERSION)" npm run build


# ============================================
# Stage 2: Python Runtime (Slim + Optimized)
# ============================================
FROM --platform=$TARGETPLATFORM python:3.13-slim AS app

ARG TARGETPLATFORM
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install system dependencies in one layer and clean up
# - git: Git storage backend
# - libpq-dev: PostgreSQL client library
# - gcc: Compile psycopg2-binary
# - openssl: TLS support
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libpq-dev \
    gcc \
    openssl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Install uv package manager
RUN pip install --no-cache-dir uv

# Install Python dependencies (separate layer for better caching)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code (changes frequently, last layer)
COPY main.py ./
COPY config.json ./
COPY VERSION ./
COPY api ./api
COPY services ./services
COPY utils ./utils
COPY scripts ./scripts

# Copy frontend build artifacts from stage 1
COPY --from=web-build /app/web/out ./web_dist

# Create data directory (runtime will mount volume here)
RUN mkdir -p /app/data

EXPOSE 80

# Health check (optional but recommended for production)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:80/health')" || exit 1

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80", "--access-log"]
