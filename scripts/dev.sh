#!/bin/bash
#
# Development helper script for nodus-adk-runtime
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

function info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

function warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

function error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

function check_venv() {
    if [ ! -d "venv" ]; then
        warn "Virtual environment not found. Creating..."
        python3 -m venv venv
        source venv/bin/activate
        pip install -e ".[dev]"
        info "Virtual environment created and dependencies installed"
    else
        source venv/bin/activate
    fi
}

case "$1" in
    install)
        info "Installing dependencies..."
        check_venv
        pip install -e ".[dev]"
        info "Dependencies installed"
        ;;
    
    run)
        info "Starting development server..."
        check_venv
        python -m nodus_adk_runtime.server
        ;;
    
    test)
        info "Running tests..."
        check_venv
        pytest "${@:2}"
        ;;
    
    lint)
        info "Running linters..."
        check_venv
        ruff check src/
        black --check src/
        mypy src/
        ;;
    
    format)
        info "Formatting code..."
        check_venv
        black src/
        ruff check --fix src/
        ;;
    
    clean)
        info "Cleaning build artifacts..."
        rm -rf build/ dist/ *.egg-info .pytest_cache .coverage htmlcov/ .tox/
        find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
        info "Cleaned"
        ;;
    
    *)
        echo "Usage: $0 {install|run|test|lint|format|clean}"
        echo ""
        echo "Commands:"
        echo "  install  - Install dependencies"
        echo "  run      - Start development server"
        echo "  test     - Run tests"
        echo "  lint     - Run linters"
        echo "  format   - Format code"
        echo "  clean    - Clean build artifacts"
        exit 1
        ;;
esac

