# Kryten-Moderator — Project Guidelines

Kryten-Moderator is the **chat moderation microservice** in the Kryten ecosystem. It subscribes to CyTube chat/user events over NATS, applies an extensible rule engine (pattern matching, user tracking, IP correlation), and issues moderation actions through `KrytenClient`.

## Architecture
- Event-driven microservice on a **NATS message bus**. Never call other services over direct HTTP — the only HTTP surface in the ecosystem is `kryten-api-gate`.
- Use the shared **`kryten-py`** library (`KrytenClient`) for all NATS, lifecycle, health, and KV state — do not use raw `nats-py`.
- Subscribe to events on `kryten.events.{domain}.{channel}.{event_type}` (normalized: lowercase, dots stripped). Handle commands on the single subject `kryten.moderator.command`, dispatching on the `command` field and replying `{"service","command","success",...}`.
- Shared state via JetStream KV buckets `kryten_{channel|service}_{type}`: bind read-only with `get_kv_store`; only the owning service creates via `get_or_create_kv_store`.
- Ecosystem contracts: [../KRYTEN_ARCHITECTURE.md](../KRYTEN_ARCHITECTURE.md), [../kryten-py/COMMAND_PROTOCOL.md](../kryten-py/COMMAND_PROTOCOL.md), [../kryten-py/STATE_MANAGEMENT.md](../kryten-py/STATE_MANAGEMENT.md), [../kryten-py/ERROR_HANDLING.md](../kryten-py/ERROR_HANDLING.md). See also [docs/nats-api.md](docs/nats-api.md).

## Build, Test & Conventions
Shared ecosystem rules (uv build/test, config auto-discovery, versioning, commit
style, NATS/KV patterns, contract-change policy): see
[../KRYTEN_CONVENTIONS.md](../KRYTEN_CONVENTIONS.md). Repo specifics:
- **Python 3.10+**; mypy target `uv run mypy kryten_moderator`.
- Config: `/etc/kryten/kryten-moderator/config.json` (JSON auto-discovery).
- **Moderation actions affect real users** — be conservative, log decisions, keep
  rule changes auditable. Ban-sync and tombstone TTLs are contract-sensitive.
- `kryten.moderator.command` command set, event shape, and KV schema are the
  contract surface — keep backward compatible and version/document any break.
- See [CONTRIBUTING.md](CONTRIBUTING.md).
