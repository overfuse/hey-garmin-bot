# syntax=docker/dockerfile:1

# --- builder: resolve & install locked deps into /app/.venv with uv ----------
FROM python:3.13-slim AS builder

# Pinned uv binary — track an explicit version, never :latest, for reproducible
# builds and a stable supply chain.
COPY --from=ghcr.io/astral-sh/uv:0.7.20 /uv /bin/uv

# Compile to .pyc at install time; copy (not hardlink) so the venv is fully
# self-contained and can be lifted into the runtime stage.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies only (not the project) so editing app code doesn't bust
# this cached layer. --frozen makes the build fail if uv.lock is out of sync
# with pyproject.toml, guaranteeing the image matches the lock exactly.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# --- runtime: just CPython + the baked venv (no uv, no build tools) -----------
FROM python:3.13-slim

WORKDIR /app

# Put the venv first on PATH so `python` is the project interpreter (this is
# what docker-compose's `command: ["python", "bot.py"]` resolves to).
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv
COPY . .

CMD ["python", "bot.py"]
