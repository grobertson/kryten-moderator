# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.2] - 2026-07-06

### Fixed

- **Correct ban-command comment in `_enforce_moderation`**: removed incorrect claim
  that Cytube bans are IP-based; the `ban` command uses the username as the key
  identifier. Whether it accepts an absent user is unverified (only confirmed for PMs).
- **Clarify `_apply_action_if_online` docstring**: smute/mute require user presence;
  ban offline behaviour is unverified. NATS KV entry remains the guaranteed
  enforcement path regardless of what the immediate command does.

## [0.7.1] - 2026-07-04

### Fixed

- **Ban enforcement uses `ban_user()` instead of `kick_user()`**: `_enforce_moderation`
  and `_apply_action_if_online` previously called `kick_user()` for `ban` entries.
  Kicks are session-level only — the user could rejoin immediately. Both now call
  `client.ban_user()` which registers a persistent Cytube IP ban.

- **Cytube ban list synced into moderator on startup**: Added a `banlist` event handler
  and a startup `requestBanlist` call for each configured channel. Bans placed directly
  in Cytube's admin UI are now imported into the moderator's NATS KV store
  (`moderator="system:cytube_sync:<bannedby>"`). Existing moderator entries are never
  overwritten.

## [0.7.0] - 2026-07-04

### Added

- **NATS API reference** (`docs/nats-api.md`): standalone consumer-facing
  documentation covering all commands with full request/response schemas,
  integration notes, suggested REST endpoint mapping, and error → HTTP status
  guidance for kryten-api-gate

- **`users.recent` NATS command**: rolling in-memory user connection history
  (default 12 h retention, configurable via `moderation.history_retention_hours`)
  Returns one record per user with a nested sessions list including
  `joined_at`, `left_at`, `duration_seconds`, masked IP, and `message_count`,
  plus the user's current `moderation_action`. Designed for identifying
  "driveby" accounts. Query window is a request argument (default 60 min)

- **`_emit_event` stub** in `ModeratorCommandHandler`: no-op placeholder for
  future bidirectional event publishing to `kryten.moderator.event.<type>`;
  called from enforcement points, ready for v0.8.0 wiring

- **`moderation.history_retention_hours`** config key (default `12`) in
  `config.example.json`

### Changed

- **kryten-py upgraded** from `>=0.13.1` to `>=0.17.0`. Version 0.17.0 adds
  automatic delay + jitter throttling on all outgoing `send_pm()` and
  `send_chat()` calls (`chat_min_delay: 1.0 s`, `chat_jitter: 0.5 s`).
  Both tuning knobs are now exposed in `config.example.json`

### Removed

- **In-chat PM command handler** (`kryten_moderator/pm_handler.py` and
  `tests/test_pm_handler.py`): replaced by the NATS request/reply API which
  kryten-api-gate exposes as REST endpoints, following the pattern established
  by the rest of the Kryten ecosystem

## [0.6.5] - 2026-07-02

### Fixed

- **CI**: Disable Sigstore attestations in PyPI publish step; when using
  `workflow_call`, the attestation Build Config URI mismatches between
  the build job and publish job, causing a `400 Bad Request` from PyPI

## [0.6.4] - 2026-07-02

### Fixed

- **CI**: Use `workflow_call` to invoke `python-publish.yml` from `release.yml`
  so the OIDC `job_workflow_ref` claim matches the PyPI trusted publisher
  (which was configured for `python-publish.yml`, not `release.yml`)

## [0.6.3] - 2026-07-01

### Fixed

- **CI**: Consolidated PyPI publish into `release.yml` as a `pypi-publish` job
  that runs immediately after the `release` job, eliminating the broken
  cross-workflow trigger (GitHub Actions does not fire `push: tags` or
  `release: published` events when a workflow creates them via `GITHUB_TOKEN`)

## [0.6.2] - 2026-07-01

### Fixed

- **CI**: `release.yml` now extracts the matching `CHANGELOG.md` section for the
  released version and uses it as the GitHub Release body, replacing the
  auto-generated commit-history notes

## [0.6.1] - 2026-07-01

### Changed

- **kryten-py dependency**: Bumped minimum requirement from `>=0.9.11` to `>=0.13.1`

- **KV store API modernization**: Updated all three KV-backed managers to use the
  current kryten-py patterns
  - `ip_manager.py`, `moderation_list.py`, `pattern_manager.py` now call
    `client.get_or_create_kv_store()` (canonical name) instead of the deprecated
    `client.get_or_create_kv_bucket()` alias
  - KV read/write operations (`kv_get`, `kv_put`, `kv_delete`, `kv_keys`) now call
    the module-level helper functions directly with the stored `self._kv` handle
    rather than routing through client-level wrappers that re-fetched the bucket
    on every call

### Fixed

- **Tests**: Updated all mock fixtures to reflect the new KV API — `mock_kv` now
  provides `.keys`, `.get`, `.put`, `.delete` async mocks directly on the KV store
  object; `mock_client.get_or_create_kv_store` returns this mock rather than the
  old per-operation `client.kv_*` stubs

### Removed

- Dead code: removed commented-out future-moderation stubs and `TODO` comment
  blocks from `_handle_chat_message` and `_handle_user_leave`; these handlers
  are functional and upcoming features are tracked in the phase specs

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
