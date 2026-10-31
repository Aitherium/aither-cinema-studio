# Aither Cinema Studio -- the backend + built UI as one container.
#
#   podman build -t aither-cinema-studio -f Containerfile .
#   podman run --rm --network=host -v cinema-data:/data aither-cinema-studio
#
# Binds 127.0.0.1:8765. With host networking it is reachable from this machine
# only, and it reaches a ComfyUI on this machine at 127.0.0.1:8188 (set
# COMFY_BASE_URL / COMFY_API_KEY to point elsewhere). Settings ride the same
# environment variables as scripts/start.sh; see README "Run it yourself".

# ---- stage 1: build the web UI --------------------------------------------
FROM docker.io/library/node:20-slim AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- stage 2: the backend -------------------------------------------------
FROM docker.io/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CINEMA_DATA_DIR=/data \
    CINEMA_CORS_ORIGINS=https://aitherium.github.io
# ffmpeg cuts sequences and muxes music
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY server/ /app/server/
COPY docs/ /app/docs/
COPY workflows/ /app/workflows/
# app.py serves <repo>/web/dist, and <repo> is two levels above server/cinema
COPY --from=web /app/web/dist /app/web/dist
RUN pip install -e /app/server
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request as r; r.urlopen('http://127.0.0.1:8765/api/health', timeout=4).close()"
CMD ["python", "-m", "uvicorn", "cinema.app:app", "--host", "127.0.0.1", "--port", "8765"]
