# Kryten-Moderator — Project Guidelines

Kryten-Moderator is the **chat moderation microservice** in the Kryten ecosystem. It subscribes to CyTube chat/user events over NATS, applies an extensible rule engine (pattern matching, user tracking, IP correlation), and issues moderation actions through `KrytenClient`.

## Architecture
- Event-driven microservice on a **NATS message bus**. Never call other services over direct HTTP — the only HTTP surface in the ecosystem is `kryten-api-gate`.
- Use the shared **`kryten-py`** library (`KrytenClient`) for all NATS, lifecycle, health, and KV state — do not use raw `nats-py`.
- Subscribe to events on `kryten.events.{domain}.{channel}.{event_type}` (normalized: lowercase, dots stripped). Handle commands on the single subject `kryten.moderator.command`, dispatching on the `command` field and replying `{"service","command","success",...}`.
- Shared state via JetStream KV buckets `kryten_{channel|service}_{type}`: bind read-only with `get_kv_store`; only the owning service creates via `get_or_create_kv_store`.
- Ecosystem contracts: [../KRYTEN_ARCHITECTURE.md](../KRYTEN_ARCHITECTURE.md), [../kryten-py/COMMAND_PROTOCOL.md](../kryten-py/COMMAND_PROTOCOL.md), [../kryten-py/STATE_MANAGEMENT.md](../kryten-py/STATE_MANAGEMENT.md), [../kryten-py/ERROR_HANDLING.md](../kryten-py/ERROR_HANDLING.md). See also [docs/nats-api.md](docs/nats-api.md).

## Build and Test
Run from the repo root (uv-managed):
- Install deps: `uv sync`
- Format: `uv run black .`
- Lint (autofix): `uv run ruff check --fix .`
- Types: `uv run mypy kryten_moderator`
- Tests: `uv run pytest` (add `--cov=kryten_moderator --cov-report=term-missing` for coverage)

Run all four before committing. Do not bypass checks (`--no-verify`).

## Conventions
- Python 3.10+, 100% `async`/`await`, Pydantic v2 config. black/ruff `line-length = 100` (E501 ignored). pytest `asyncio_mode = "auto"`.
- **Event handlers must catch and log exceptions — never raise into the event loop.** Rely on `kryten-py` auto-reconnect; don't hand-roll reconnect logic.
- Config is JSON with auto-discovery: `--config` flag → `/etc/kryten/kryten-moderator/config.json` → `./config.json`. Keep `config.example.json` in sync; never hardcode values or NATS subjects.
- Moderation actions can affect real users — be conservative, log decisions, and make rule changes auditable. Ban-sync and tombstone TTLs are contract-sensitive.
- Version lives only in `pyproject.toml [project] version`. Update `CHANGELOG.md` (Keep-a-Changelog + SemVer, ISO dates) for versioned changes.
- Commit prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:`. Branches: `feature/…`, `fix/…`. See [CONTRIBUTING.md](CONTRIBUTING.md).
