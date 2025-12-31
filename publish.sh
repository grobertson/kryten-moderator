#!/bin/bash
# Local publishing script for Kryten Moderator
# Standardized for uv and pyproject.toml SSOT

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Default values
CLEAN=false
BUILD=false
TEST_PYPI=false
PUBLISH=false

# Print with color
print_info() {
    echo -e "${GREEN}[INFO] $1${NC}"
}

print_warn() {
    echo -e "${YELLOW}[WARN] $1${NC}"
}

print_error() {
    echo -e "${RED}[ERROR] $1${NC}"
}

# Parse arguments
for arg in "$@"
do
    case $arg in
        --clean)
        CLEAN=true
        shift
        ;;
        --build)
        BUILD=true
        shift
        ;;
        --testpypi)
        TEST_PYPI=true
        shift
        ;;
        --publish)
        PUBLISH=true
        shift
        ;;
    esac
done

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    print_error "uv is not installed. Please install it first."
    exit 1
fi

# Get version from pyproject.toml
if [ ! -f "pyproject.toml" ]; then
    print_error "pyproject.toml not found!"
    exit 1
fi

VERSION=$(grep -m1 'version = "' pyproject.toml | cut -d '"' -f 2)
print_info "Package version: $VERSION"

# Clean
if [ "$CLEAN" = true ]; then
    print_info "Cleaning build artifacts..."
    rm -rf dist/ build/ *.egg-info/
fi

# Build
if [ "$BUILD" = true ]; then
    print_info "Building package with uv..."
    uv build
fi

# Publish to TestPyPI
if [ "$TEST_PYPI" = true ]; then
    print_info "Publishing to TestPyPI..."
    uv publish --publish-url https://test.pypi.org/legacy/
fi

# Publish to PyPI
if [ "$PUBLISH" = true ]; then
    print_warn "You are about to publish to PyPI. Are you sure? (y/N)"
    read -r response
    if [[ "$response" =~ ^([yY][eE][sS]|[yY])+$ ]]; then
        print_info "Publishing to PyPI..."
        uv publish
    else
        print_info "Publishing cancelled."
    fi
fi
