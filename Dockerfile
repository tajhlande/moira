# ---- Stage 1: Frontend build ----
FROM node:22-alpine AS frontend
WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ .
RUN npm run build

# ---- Stage 2: Backend runtime ----
FROM python:3.13-slim

WORKDIR /app

# Install uv from the official image (single static binary)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install backend dependencies (layer cached separately from source code)
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

# Copy backend source
COPY backend/ ./

# Copy built frontend from Stage 1
COPY --from=frontend /app/frontend/dist /app/static/

# Tell the app where to find the frontend static files
ENV MOIRA_STATIC_DIR=/app/static
# Cache the embedding model inside the image (predictable location)
ENV HF_HOME=/app/.cache

# Pre-download the sentence-transformers embedding model so first container
# start is fast and doesn't require internet access.
RUN uv run python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

EXPOSE 8000

CMD ["uv", "run", "python", "-m", "uvicorn", "moira.main:app", "--host", "0.0.0.0", "--port", "8000"]
