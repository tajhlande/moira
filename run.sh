#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
CONFIG_FILE="${MOIRA_CONFIG_FILE:-$REPO_ROOT/config/moira-config.yaml}"
DATA_DIR="${MOIRA_DATA_DIR:-$REPO_ROOT/data}"

usage() {
    echo "Usage: ./run.sh <command>"
    echo ""
    echo "Commands:"
    echo "  setup           Install all dependencies"
    echo "  dev             Start backend + frontend dev servers"
    echo "  dev:backend     Start backend only"
    echo "  dev:frontend    Start frontend only"
    echo "  build           Build frontend for production"
    echo "  prod            Build frontend + start backend serving everything"
    echo "  test            Run all tests"
    echo "  test:backend    Run backend tests"
    echo "  test:frontend   Run frontend tests"
    echo "  lint            Run all linters"
    echo "  lint:backend    Run backend linter"
    echo "  lint:frontend   Run frontend linter"
    echo "  format          Auto-format all code"
    echo "  format:backend  Auto-format backend code"
    echo "  format:frontend Auto-format frontend code"
}

check_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo ""
        echo "Config file not found: $CONFIG_FILE"
        echo ""
        echo "  export MOIRA_CONFIG_FILE=/path/to/moira-config.yaml"
        echo ""
        echo "Or copy config/moira-config-template.yaml to config/moira-config.yaml, edit it, and re-run."
        echo ""
        exit 1
    fi
}

check_data_dir() {
    if [ ! -d "$DATA_DIR" ]; then
        echo ""
        echo "Data directory not found: $DATA_DIR"
        echo ""
        echo "  export MOIRA_DATA_DIR=/path/to/data"
        echo "  mkdir -p \$MOIRA_DATA_DIR"
        echo ""
        echo "Or create the default directory: mkdir -p ./data"
        echo ""
        exit 1
    fi
}

cmd_setup() {
    echo "=== Setting up backend ==="
    cd "$REPO_ROOT/backend"
    uv sync

    echo "=== Setting up frontend ==="
    cd "$REPO_ROOT/frontend"
    npm install

    echo "=== Done ==="
}

cmd_dev() {
    check_config
    check_data_dir
    cmd_dev_backend &
    cmd_dev_frontend &
    wait
}

cmd_dev_backend() {
    check_config
    check_data_dir
    cd "$REPO_ROOT/backend"
    source "$REPO_ROOT/.env"
    MOIRA_CONFIG_FILE="$CONFIG_FILE" MOIRA_DATA_DIR="$DATA_DIR" MOIRA_SECRETS_KEY="$MOIRA_SECRETS_KEY" \
        uv run python -m uvicorn moira.main:app --reload --port 8000
}

cmd_dev_frontend() {
    cd "$REPO_ROOT/frontend"
    npm run dev
}

cmd_build() {
    echo "=== Building frontend ==="
    cd "$REPO_ROOT/frontend"
    npm run build
    echo "=== Build complete: frontend/dist/ ==="
}

cmd_prod() {
    check_config
    check_data_dir
    cmd_build
    echo "=== Starting MOiRA (production mode) ==="
    cd "$REPO_ROOT/backend"
    source "$REPO_ROOT/.env"
    MOIRA_CONFIG_FILE="$CONFIG_FILE" MOIRA_DATA_DIR="$DATA_DIR" MOIRA_SECRETS_KEY="$MOIRA_SECRETS_KEY" \
        uv run python -m uvicorn moira.main:app --host 0.0.0.0 --port 8000
}

cmd_test() {
    cmd_test_backend
    cmd_test_frontend
}

cmd_test_backend() {
    cd "$REPO_ROOT/backend"
    uv run python -m pytest tests/ -v
}

cmd_test_frontend() {
    cd "$REPO_ROOT/frontend"
    npx vitest run
}

cmd_lint() {
    cmd_lint_backend
    cmd_lint_frontend
}

cmd_lint_backend() {
    cd "$REPO_ROOT/backend"
    uv run ruff check .
    uv run ruff format --check .
}

cmd_lint_frontend() {
    cd "$REPO_ROOT/frontend"
    npm run lint
}

cmd_format() {
    cmd_format_backend
    cmd_format_frontend
}

cmd_format_backend() {
    cd "$REPO_ROOT/backend"
    uv run ruff check --fix .
    uv run ruff format .
}

cmd_format_frontend() {
    cd "$REPO_ROOT/frontend"
    npm run lint:fix
}

case "${1:-}" in
    setup)           cmd_setup ;;
    dev)             cmd_dev ;;
    dev:backend)     cmd_dev_backend ;;
    dev:frontend)    cmd_dev_frontend ;;
    build)           cmd_build ;;
    prod)            cmd_prod ;;
    test)            cmd_test ;;
    test:backend)    cmd_test_backend ;;
    test:frontend)   cmd_test_frontend ;;
    lint)            cmd_lint ;;
    lint:backend)    cmd_lint_backend ;;
    lint:frontend)   cmd_lint_frontend ;;
    format)          cmd_format ;;
    format:backend)  cmd_format_backend ;;
    format:frontend) cmd_format_frontend ;;
    *)               usage ;;
esac
