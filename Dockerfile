FROM python:3.11-slim-bookworm

# FFmpeg pinned to Debian bookworm stable package (epoch 7, version 5.1.8).
# Exact apt package: 7:5.1.8-0+deb12u1 — confirmed against bookworm repos 2026-05.
# To upgrade: run `apt-cache policy ffmpeg` inside a fresh bookworm container to get
# the current version string, update the line below, test in-container, then update
# this comment. Do NOT remove the pin — see CLAUDE.md Lessons Learned.
RUN apt-get update && apt-get install -y --no-install-recommends \
        "ffmpeg=7:5.1.8-0+deb12u1" \
    && rm -rf /var/lib/apt/lists/*

# Install uv from its official pinned image layer
COPY --from=ghcr.io/astral-sh/uv:0.4.30 /uv /uvx /bin/

WORKDIR /app

# Dependency files first — maximises Docker layer cache hits
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Sanity-check that ffmpeg is callable
RUN ffmpeg -version 2>&1 | head -1

# Migrations run before the service loop so Railway health check won't pass
# until the schema is current — a safe deployment gate.
CMD ["sh", "-c", "uv run alembic upgrade head && uv run python -m src.main"]
