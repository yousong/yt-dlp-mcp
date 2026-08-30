FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    mpv \
    pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
RUN uv sync --frozen --no-dev

VOLUME ["/data/cookies", "/data/store"]

ENV PULSE_SERVER="" \
    YTDLP_MCP_TRANSPORT=streamable-http \
    YTDLP_MCP_HOST=0.0.0.0 \
    YTDLP_MCP_PORT=8080 \
    YTDLP_MCP_COOKIE_DIR=/data/cookies \
    YTDLP_MCP_STORE_DIR=/data/store \
    MPV_SOCKET=/tmp/mpv-socket

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/mcp')" || exit 1

ENTRYPOINT ["uv", "run", "python", "-m", "yt_dlp_mcp"]
