#!/usr/bin/env pwsh
# Start script for kryten-moderator service

$ErrorActionPreference = "Stop"

function Test-VenvValid {
    param([string]$Path)

    if (-not (Test-Path $Path)) { return $false }

    $pythonExe = Join-Path $Path "Scripts\python.exe"
    $pipExe = Join-Path $Path "Scripts\pip.exe"
    $pyvenvCfg = Join-Path $Path "pyvenv.cfg"

    if (-not (Test-Path $pyvenvCfg)) { return $false }
    if (-not (Test-Path $pythonExe) -or -not (Test-Path $pipExe)) { return $false }

    try {
        & $pythonExe -c "import sys" *> $null
        return $true
    } catch {
        return $false
    }
}

function New-VirtualEnvironment {
    param([string]$Path)

    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCmd) {
        & uv venv $Path
    } else {
        & python -m venv $Path
    }
}

# Clear PYTHONPATH to avoid conflicts
$env:PYTHONPATH = ""

# Change to script directory
Set-Location $PSScriptRoot

$venvPath = Join-Path $PSScriptRoot ".venv"
if (-not (Test-VenvValid $venvPath)) {
    if (Test-Path $venvPath) {
        try {
            Remove-Item -Recurse -Force $venvPath -ErrorAction Stop
        } catch {
            throw "Could not remove corrupted .venv. Close terminals/processes using it and retry."
        }
    }

    New-VirtualEnvironment $venvPath

    if (-not (Test-VenvValid $venvPath)) {
        throw "Failed to create a valid .venv at $venvPath"
    }
}

# Activate virtual environment if it exists
if (Test-Path ".venv/Scripts/Activate.ps1") {
    & .venv/Scripts/Activate.ps1
}

# Start the service
poetry run kryten-moderator --config config.json
