#!/usr/bin/env pwsh
# Start script for kryten-moderator service

$ErrorActionPreference = "Stop"

# Clear PYTHONPATH to avoid conflicts
$env:PYTHONPATH = ""

# Change to script directory
Set-Location $PSScriptRoot

# Activate virtual environment if it exists
if (Test-Path ".venv/Scripts/Activate.ps1") {
    & .venv/Scripts/Activate.ps1
}

# Start the service
poetry run kryten-moderator --config config.json
