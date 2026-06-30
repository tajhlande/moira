# Docker Deployment Plan

## Architecture

- **Single container** — FastAPI serves both the API (`/api/...`) and built frontend
  static files (everything else)
- **Same-origin** — no `VITE_API_URL` needed, no CORS issues. External nginx reverse
  proxy handles TLS termination and forwards to this container.
- **Volume mount** — `data/` directory persisted on the host for SQLite + LanceDB

## Files to Create

| File | Purpose |
|---|---|
| `Dockerfile` | Multi-stage: Node build for frontend → Python runtime with `uv`, FastAPI serves static files via `StaticFiles` mount |
| `.dockerignore` | Exclude `.venv/`, `node_modules/`, `data/`, `__pycache__/`, `.env`, `config/moira-config.yaml` |
| `docker-compose.yml` | Single service definition with volume mount, env vars, restart policy |
| `deploy.sh` | Script for the docker host: build, stop old, start new, health check |

## Code Changes

| Change | File | Reason |
|---|---|---|
| Mount `StaticFiles` for frontend dist | `backend/moira/main.py` | Serve frontend from same origin as API |
| Add `/api/health` endpoint | `backend/moira/main.py` | Docker HEALTHCHECK + deploy script verification |
| Fix `banner.txt` path to use `__file__`-relative resolution | `backend/moira/main.py` | Docker WORKDIR may differ from dev |
| Update CORS origins comment | `config/moira-config-template.yaml` | Document that same-origin doesn't need CORS |

## Dockerfile

Multi-stage build:

### Stage 1: Frontend build (node:22-alpine)
- `COPY frontend/package.json frontend/package-lock.json`
- `npm ci`
- `COPY frontend/`
- `npm run build` → outputs to `frontend/dist/`

### Stage 2: Backend runtime (python:3.13-slim)
- Install `uv`
- `COPY backend/pyproject.toml backend/uv.lock`
- `uv sync --frozen --no-dev`
- `COPY backend/`
- `COPY --from=0 /app/frontend/dist/ /app/static/`
- Pre-download `all-MiniLM-L6-v2` sentence-transformers model via a short Python script
  (so first container start doesn't need internet access or a 30s+ download)
- `EXPOSE 8000`
- `CMD: uvicorn moira.main:app --host 0.0.0.0 --port 8000`

### Static file serving in main.py

After building all API routes, mount `StaticFiles` at `/` (catch-all, after API routes):

```python
from starlette.staticfiles import StaticFiles
import os

static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
```

This must be mounted LAST so `/api/...` routes take precedence. `html=True` enables
serving `index.html` for SPA routing.

### Health check endpoint

```python
@app.get("/api/health")
async def health():
    return {"status": "ok"}
```

### Banner path fix

The banner currently loads via a relative path that assumes CWD is `backend/`.
Change to use `__file__`-relative resolution:

```python
banner_path = Path(__file__).parent / "resources" / "banner.txt"
```

## docker-compose.yml

```yaml
services:
  moira:
    build: .
    container_name: moira
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./config/moira-config.yaml:/app/config/moira-config.yaml:ro
    environment:
      - MOIRA_CONFIG_FILE=/app/config/moira-config.yaml
      - MOIRA_DATA_DIR=/app/data
      - MOIRA_SECRETS_KEY=${MOIRA_SECRETS_KEY}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
```

## deploy.sh

Script to run on the docker host:

1. `cd` to the project directory (where docker-compose.yml lives)
2. `docker compose build`
3. `docker compose stop` (stop old container, preserves data volume)
4. `docker compose up -d`
5. Wait for health check to pass
6. Print container status and URL

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "Building moira..."
docker compose build

echo "Stopping old container..."
docker compose stop moira 2>/dev/null || true

echo "Starting moira..."
docker compose up -d

echo "Waiting for health check..."
timeout 60 bash -c 'until docker compose exec moira python -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/api/health\")" 2>/dev/null; do sleep 2; done'

echo "MOiRA is running at http://localhost:8000"
docker compose ps
```

## .dockerignore

```
.git
.env
**/.venv
**/node_modules
**/__pycache__
**/*.pyc
**/.pytest_cache
**/.ruff_cache
**/dist
data/
config/moira-config.yaml
.DS_Store
*.db
```

## What's needed on the docker host

1. Docker + docker compose plugin
2. `moira-config.yaml` with inference endpoints pointing to the local LLM host
3. `MOIRA_SECRETS_KEY` value (in a `.env` file alongside `docker-compose.yml`, or set in environment)
4. Existing nginx config updated to proxy to the container (e.g., `proxy_pass http://moira:8000`)

## External dependencies (not included in Docker)

- **LLM inference endpoints** — already running on your inference host
- **SearXNG** — already running as a separate service
- **Nginx reverse proxy** — already running for TLS termination

## Notes

- The sentence-transformers model (`all-MiniLM-L6-v2`, ~80MB) is baked into the
  image during build. First container start will be fast.
- SQLite and LanceDB data live in the mounted `data/` volume. Container recreation
  does NOT lose data.
- Config is mounted read-only from the host. Changes require container restart
  (`docker compose restart moira`), not a rebuild.
- The `MOIRA_SECRETS_KEY` should be stored in a `.env` file next to
  `docker-compose.yml`. This file is gitignored and should never be committed.
