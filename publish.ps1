<#
.SYNOPSIS
    Local publishing script for Kryten Moderator
    Standardized for uv and pyproject.toml SSOT

.DESCRIPTION
    Handles cleaning, building, and publishing the package to PyPI or TestPyPI
    using uv.

.PARAMETER Clean
    Removes dist/ and build/ directories before building

.PARAMETER Build
    Builds the package using uv build

.PARAMETER TestPyPI
    Publishes to TestPyPI

.PARAMETER Publish
    Publishes to PyPI (requires confirmation)
#>

param (
    [Switch]$Clean,
    [Switch]$Build,
    [Switch]$TestPyPI,
    [Switch]$Publish
)

$ErrorActionPreference = "Stop"

# Colors
$Green = [ConsoleColor]::Green
$Yellow = [ConsoleColor]::Yellow
$Red = [ConsoleColor]::Red
$Reset = [ConsoleColor]::White

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor $Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor $Yellow
}

function Write-ErrorMsg {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor $Red
}

# Check for uv
if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
    Write-ErrorMsg "uv is not installed. Please install it first."
    exit 1
}

# Get version
if (-not (Test-Path "pyproject.toml")) {
    Write-ErrorMsg "pyproject.toml not found!"
    exit 1
}

$VersionLine = Select-String -Path "pyproject.toml" -Pattern 'version = "(.*)"' | Select-Object -First 1
if ($VersionLine -match 'version = "(.*)"') {
    $Version = $Matches[1]
    Write-Info "Package version: $Version"
} else {
    Write-ErrorMsg "Could not determine version from pyproject.toml"
    exit 1
}

# Clean
if ($Clean) {
    Write-Info "Cleaning build artifacts..."
    if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
    if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
    Get-ChildItem -Filter "*.egg-info" -Recurse | Remove-Item -Recurse -Force
}

# Build
if ($Build) {
    Write-Info "Building package with uv..."
    uv build
}

# Publish to TestPyPI
if ($TestPyPI) {
    Write-Info "Publishing to TestPyPI..."
    uv publish --publish-url https://test.pypi.org/legacy/
}

# Publish to PyPI
if ($Publish) {
    Write-Warn "You are about to publish to PyPI. Are you sure? (y/N)"
    $Response = Read-Host
    if ($Response -match "^[yY]") {
        Write-Info "Publishing to PyPI..."
        uv publish
    } else {
        Write-Info "Publishing cancelled."
    }
}
