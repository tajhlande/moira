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
| `Dockerfile` | Multi-stage: Node frontend build → Python runtime with `uv` (CPU-only torch), FastAPI serves static files via `SPAStaticFiles` mount |
| `.dockerignore` | Exclude `.venv/`, `node_modules/`, `data/`, `__pycache__/`, `.env`, `config/moira-config.yaml` |
| `docker-compose.yml` | Service def: `image: ghcr.io/tajhlande/moira` + `build: .`, volumes, env, healthcheck |
| `deploy.sh` | Docker host script: defaults to `pull` from GHCR, `--build` for local source build |
| `.github/workflows/docker-publish.yml` | CI: build multi-arch (amd64+arm64) on tag push, publish to GHCR |

## Code Changes (DONE)

All of these are already implemented in `backend/moira/main.py`:

| Change | File | Reason |
|---|---|---|
| `_resolve_static_dir()` + `SPAStaticFiles` mount | `backend/moira/main.py` | Serve frontend from same origin as API; SPA fallback for client-side routing |
| `/api/health` endpoint | `backend/moira/main.py` | Docker HEALTHCHECK + deploy script verification |
| `banner.txt` path via `__file__`-relative resolution | `backend/moira/main.py` | Docker WORKDIR may differ from dev |
| `MOIRA_STATIC_DIR` env var support | `backend/moira/main.py` | Docker container tells the app where the frontend dist lives |

The Dockerfile must set `MOIRA_STATIC_DIR=/app/static` (see below) so the
container finds the frontend. Without it, the fallback checks
`<repo_root>/frontend/dist` which won't exist in the container image.

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
  - `pyproject.toml` maps torch to the CPU-only index (see [CPU-only Torch](#cpu-only-torch)),
    so `uv sync` pulls CPU wheels automatically — no extra flags needed
- `COPY backend/`
- `COPY --from=0 /app/frontend/dist/ /app/static/`
- `ENV MOIRA_STATIC_DIR=/app/static` — so `_resolve_static_dir()` finds the frontend
- Pre-download `all-MiniLM-L6-v2` sentence-transformers model via a short Python script
  (so first container start doesn't need internet access or a 30s+ download)
- `EXPOSE 8000`
- `CMD: uvicorn moira.main:app --host 0.0.0.0 --port 8000`

### Static file serving in main.py (ALREADY IMPLEMENTED)

`main.py` already has `_resolve_static_dir()` which checks (in order):
1. `MOIRA_STATIC_DIR` env var → used by Docker
2. `<repo_root>/frontend/dist` → used by `./run.sh prod` (standalone production build)

And `SPAStaticFiles` (a `StaticFiles` subclass) that returns `index.html` for
any 404, enabling client-side routing fallback.

The mount happens LAST in `create_app()` so `/api/...` routes take precedence.

No code changes needed — just set `MOIRA_STATIC_DIR=/app/static` in the Dockerfile.

### Health check endpoint (ALREADY IMPLEMENTED)

`/api/health` already exists in `main.py`:

```python
@app.get("/api/health")
async def health():
    return {"status": "ok"}
```

### Banner path fix (ALREADY IMPLEMENTED)

Already uses `__file__`-relative resolution in `main.py`.

### CPU-only Torch

The Dockerfile and default `pyproject.toml` use CPU-only PyTorch wheels, which drop
~1GB of CUDA binaries that the embedding model (`all-MiniLM-L6-v2`) does not need.

Configuration in `pyproject.toml`:

```toml
[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[tool.uv.sources]
torch = { index = "pytorch-cpu" }
```

This maps `torch` exclusively to the CPU index. `uv sync`, Docker builds, and local
dev all get CPU wheels by default — no extra flags.

**GPU override:** users who need CUDA torch for other workloads can edit the index URL
in `pyproject.toml` (e.g., `https://download.pytorch.org/whl/cu124`) or remove the
`[tool.uv.sources]` mapping to fall back to PyPI's CUDA wheel.

## docker-compose.yml

```yaml
services:
  moira:
    image: ghcr.io/tajhlande/moira:${MOIRA_VERSION:-latest}
    build: .                          # optional: build locally instead of pulling
    container_name: moira
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./config/moira-config.yaml:/app/config/moira-config.yaml:ro
    environment:
      - MOIRA_CONFIG_FILE=/app/config/moira-config.yaml
      - MOIRA_DATA_DIR=/app/data
      - MOIRA_STATIC_DIR=/app/static
      - MOIRA_SECRETS_KEY=${MOIRA_SECRETS_KEY}
      - HF_TOKEN=${HF_TOKEN}
      - MOIRA_PROMPT_FILE=${MOIRA_PROMPT_FILE}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
```

- `image:` — the GHCR registry image. Set `MOIRA_VERSION` to pin a release
  (e.g., `MOIRA_VERSION=v0.1.0 docker compose up`).
- `build: .` — lets users build from source locally via `docker compose build`
  instead of pulling from the registry.
- `container_name: moira` — the runtime container name (stays just `moira`,
  independent of the image name).
- `docker compose pull` → fetch from GHCR.
- `docker compose build` → build from local source.

## deploy.sh

Script to run on the docker host. Two modes:

| Mode | Command | What it does |
|---|---|---|
| **Pull (default)** | `./deploy.sh` | `docker compose pull` from GHCR, then restart |
| **Local build** | `./deploy.sh --build` | `docker compose build` from source, then restart |

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-pull}"

if [ "$MODE" = "--build" ]; then
    echo "Building moira from source..."
    docker compose build
elif [ "$MODE" = "pull" ]; then
    echo "Pulling moira from GHCR..."
    docker compose pull
else
    echo "Usage: ./deploy.sh [--build|pull]"
    exit 1
fi

echo "Stopping old container..."
docker compose stop moira 2>/dev/null || true

echo "Starting moira..."
docker compose up -d

echo "Waiting for health check..."
timeout 60 bash -c 'until docker compose exec moira python -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/api/health\")" 2>/dev/null; do sleep 2; done'

echo "MOiRA is running at http://localhost:8000"
docker compose ps
```

## CI/CD — GitHub Actions (`.github/workflows/docker-publish.yml`)

Builds and publishes the multi-arch image to GHCR automatically.

### Triggers

- Tag push matching `v*` (e.g., `v0.1.0`) → build + push
- Manual `workflow_dispatch` → for testing

### Strategy — native runners (not QEMU)

Uses a matrix to build each architecture on its native runner, then merges
the per-arch images into a single multi-arch manifest. QEMU emulation would
make the arm64 build 5-10x slower (PyTorch is ~800MB to install).

| Arch | Runner | ~Build time |
|---|---|---|
| `linux/amd64` | `ubuntu-latest` | ~10 min |
| `linux/arm64` | `ubuntu-24.04-arm` | ~10 min |

### Image tags

| Tag | Example | Purpose |
|---|---|---|
| `latest` | `ghcr.io/tajhlande/moira:latest` | Most recent release |
| `MAJOR.MINOR.PATCH` | `ghcr.io/tajhlande/moira:0.1.0` | Pinned version |
| `MAJOR.MINOR` | `ghcr.io/tajhlande/moira:0.1` | Latest patch in minor |
| `MAJOR` | `ghcr.io/tajhlande/moira:0` | Latest minor in major |
| `sha-<git-sha>` | `ghcr.io/tajhlande/moira:sha-abc1234` | Traceability |

### Workflow outline

```yaml
name: Publish Docker Image

on:
  push:
    tags: ['v*']
  workflow_dispatch:

permissions:
  contents: read
  packages: write

jobs:
  build:
    strategy:
      matrix:
        include:
          - platform: linux/amd64
            runner: ubuntu-latest
          - platform: linux/arm64
            runner: ubuntu-24.04-arm
    runs-on: ${{ matrix.runner }}
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: ghcr.io/tajhlande/moira
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=semver,pattern={{major}}
            type=raw,value=latest,enable={{is_default_branch}}
            type=sha,prefix=sha-
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: ${{ matrix.platform }}
          labels: ${{ steps.meta.outputs.labels }}
          outputs: type=image,name=ghcr.io/tajhlande/moira,push-by-digest=true,name-canonical=true,push=true
          cache-from: type=gha
          cache-to: type=gha,mode=max

  merge:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: ghcr.io/tajhlande/moira
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=semver,pattern={{major}}
            type=raw,value=latest,enable={{is_default_branch}}
            type=sha,prefix=sha-
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: ./Dockerfile
          platforms: linux/amd64,linux/arm64
          labels: ${{ steps.meta.outputs.labels }}
          outputs: type=image,push=true
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

> **Note:** The merge job uses `docker buildx imagetools` under the hood via
> `build-push-action`'s manifest-only mode. Each arch builds independently with
> its own digest, then the merge job combines them under the named tags.
> See [docker/build-push-action#1316](https://github.com/docker/build-push-action)
> docs for the latest manifest-merge patterns.

### Authentication

- **CI** uses the built-in `GITHUB_TOKEN` with `packages: write` permission.
  No PAT or secret configuration needed.
- **End users pulling a public image** — no authentication needed.
  `docker compose pull` works directly.
- **End users pulling a private image** — need a GitHub PAT with `read:packages`:
  ```bash
  echo $GITHUB_PAT | docker login ghcr.io -u USERNAME --password-stdin
  ```

## Architecture Support

The image builds for both `linux/amd64` and `linux/arm64` (Apple Silicon, AWS Graviton).

All native-code dependencies have pre-built wheels for both architectures — no
source compilation required:

| Package | amd64 wheel | arm64 wheel |
|---|---|---|
| torch 2.12.0 | ✅ | ✅ |
| lancedb 0.30.2 | ✅ | ✅ |
| scipy 1.17.1 | ✅ | ✅ |
| numpy 2.4.6 | ✅ | ✅ |
| cryptography 48.0.1 | ✅ | ✅ |
| pydantic-core 2.46.4 | ✅ | ✅ |
| lxml 6.1.1 | ✅ | ✅ |
| httptools, uvloop, orjson, watchfiles | ✅ | ✅ |
| lance-namespace 0.7.7 | pure Python (`py3-none-any`) | pure Python |

The frontend build (Stage 1) produces architecture-independent static files, and
the `all-MiniLM-L6-v2` embedding model weights are tensors — both work on any arch.

Using CPU-only torch (see [CPU-only Torch](#cpu-only-torch)) drops ~1GB of CUDA
binaries, reducing the image from ~2.5GB to ~1.5GB.

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

### Registry-pull deployment (recommended)

1. Docker + docker compose plugin
2. `docker-compose.yml` (download from repo — just this one file)
3. `config/moira-config.yaml` with inference endpoints pointing to the LLM host
4. `MOIRA_SECRETS_KEY` in a `.env` file alongside `docker-compose.yml`
5. Optional: `MOIRA_VERSION` to pin a specific release
6. Existing nginx config updated to proxy to the container

```bash
docker compose pull      # or: ./deploy.sh
docker compose up -d
```

No source code, Node.js, or build tools needed on the host.

### Local-build deployment

1. Docker + docker compose plugin
2. Full repo cloned to the host
3. `config/moira-config.yaml` and `MOIRA_SECRETS_KEY` (same as above)
4. `./deploy.sh --build` — builds the image from source, then starts

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

## GHCR Setup Notes

- **First push**: after the first tag push triggers the CI workflow, the package
  appears at `ghcr.io/tajhlande/moira`. Go to the package settings on GitHub to
  set visibility to **Public** (inherits repository visibility by default for
  public repos).
- **No PAT needed for CI**: the workflow uses the built-in `GITHUB_TOKEN` with
  `packages: write` permission. No additional secrets to configure.
- **Pulling**: public images can be pulled without authentication. Private images
  require `docker login ghcr.io` with a GitHub PAT that has `read:packages`.
- **Image URL**: `ghcr.io/tajhlande/moira` (determined by GitHub username `tajhlande`
  and repository name `moira`).
