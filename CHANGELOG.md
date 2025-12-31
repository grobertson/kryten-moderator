# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2025-12-31

### Changed
- **Release**: Minor version bump for coordinated ecosystem release.

## [0.3.5] - 2025-12-31

### Fixed
- **Style**: Applied black formatting to all source files to pass CI.

## [0.3.4] - 2025-12-31

### Fixed
- **CI/CD**: Standardized build and release workflows to use `uv` and trigger on tags.
- **Linting**: Fixed Ruff, Black, and Mypy issues for clean CI execution.

## [0.2.1] - 2025-12-14

### Added

- **HTTP Metrics Server**: Added Prometheus-compatible metrics endpoint
  - `GET /health` - JSON health status with service details
  - `GET /metrics` - Prometheus format metrics
  - Default port: 28284 (configurable via `metrics.port`)
  - Metrics include: events_processed, commands_processed, messages_checked, messages_flagged, users_tracked
  - Uses `BaseMetricsServer` from kryten-py for consistent infrastructure

## [0.2.0] - 2025-12-14

### Changed

- **Complete Architecture Refactor**: Modernized to match kryten-userstats patterns
  - Service now uses dict-based config like other kryten services
  - Removed legacy Config class in favor of direct JSON loading
  - Version now defined in `__init__.py` (single source of truth)
  - Version injected into config at runtime for consistency

- **Event System**: Updated to use modern kryten-py event types
  - Uses `ChatMessageEvent`, `UserJoinEvent`, `UserLeaveEvent` typed events
  - Decorator-based event handler registration (`@client.on("chatmsg")`)
  - Subscribes to `kryten.lifecycle.robot.startup` for re-registration

- **NATS Command Handler**: Added `kryten.moderator.command` subscription
  - Supports `system.health` and `system.stats` commands
  - Follows userstats pattern for request/reply handling
  - Ready for future moderation commands

- **Dependencies**: Updated kryten-py requirement to >=0.9.4

- **Config Format**: Modernized to match ecosystem standard
  - Uses `service`, `nats`, `channels` structure
  - Supports lifecycle, heartbeat, and discovery settings
  - Added `moderation` section for future features

### Added

- Statistics tracking: events_processed, commands_processed, messages_checked
- User tracking set for monitoring active users
- Uptime tracking and reporting

## [0.1.1] - 2024-12-01

### Added
- Initial skeleton implementation
- Basic service structure with KrytenClient integration
- Event handlers for `chatMsg` and `addUser` events
- Configuration management system
- CI workflow with Python 3.10, 3.11, and 3.12 support
- PyPI publishing workflow with trusted publishing
- Startup scripts for PowerShell and Bash
- Systemd service manifest
- Documentation structure

## [0.1.0] - Unreleased

Initial development release.
