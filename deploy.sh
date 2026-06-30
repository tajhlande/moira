#!/usr/bin/env bash
# deploy.sh — MOiRA Docker deployment script
#
# Usage:
#   ./deploy.sh           Pull from GHCR and restart (default)
#   ./deploy.sh --build   Build from local source and restart
#   ./deploy.sh pull      Same as default (explicit)
#
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-pull}"

case "$MODE" in
    --build)
        echo "Building moira from source..."
        docker compose build
        ;;
    pull)
        echo "Pulling moira from GHCR..."
        docker compose pull
        ;;
    *)
        echo "Usage: ./deploy.sh [--build|pull]"
        echo ""
        echo "  pull       Pull from GHCR and restart (default)"
        echo "  --build    Build from local source and restart"
        exit 1
        ;;
esac

echo "Stopping old container..."
docker compose stop moira 2>/dev/null || true

echo "Starting moira..."
docker compose up -d

echo "Waiting for health check..."
timeout 60 bash -c 'until docker compose exec moira python -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/api/health\")" 2>/dev/null; do sleep 2; done'

echo ""
echo "MOiRA is running at http://localhost:8000"
docker compose ps
