# Kryten Moderator — NATS API Reference

**Subject**: `kryten.moderator.command`  
**Protocol**: NATS request/reply (`subscribe_request_reply`)  
**Encoding**: JSON

---

## Request / Response Envelope

Every call uses the same envelope:

```json
// Request
{
  "command": "<command.name>",
  "service": "moderator",    // optional routing hint — other services may send this field; moderator rejects requests where service != "moderator"
  ... command-specific fields ...
}

// Success response
{
  "service": "moderator",
  "command": "<command.name>",
  "success": true,
  "data": { ... }
}

// Error response
{
  "service": "moderator",
  "command": "<command.name>",
  "success": false,
  "error": "<human-readable message>"
}
```

---

## Integration Notes

### Sending a request (kryten-api-gate)

```python
reply = await nats_client.request(
    "kryten.moderator.command",
    json.dumps({"command": "entry.add", "channel": "lounge", ...}).encode(),
    timeout=10.0,  # seconds — use 10 s for all commands
)
response = json.loads(reply.data)
```

Recommended timeout: **10 seconds** for all commands.

### Timestamps

All timestamps are ISO 8601 strings.  
- `entry.*` and `pattern.*` fields (`timestamp`) — stored as local-time ISO strings with no UTC offset (value of `datetime.now().isoformat()` at the service host).
- `users.recent` fields (`joined_at`, `left_at`, `last_seen`, `generated_at`) — always UTC with explicit `+00:00` offset.
- `system.ping` field (`timestamp`) — local-time ISO string.

### `domain` default

When `domain` is omitted the service uses the domain of the **first** channel listed in its `config.json`. In a single-channel deployment this can always be omitted.

### `entry.add` overwrite behaviour

If a user is already on the moderation list, `entry.add` **replaces** the existing entry with the new action, reason, and moderator. The replacement is applied immediately if the user is online.

---

## Commands

### `system.ping`

Liveness check with metadata. Use this to verify the service is reachable before issuing moderation commands.

**Request** — no additional fields required.

**Response `data`**
```json
{
  "pong": true,
  "service": "moderator",
  "version": "0.7.0",
  "uptime_seconds": 3600.0,
  "timestamp": "2026-07-04T00:00:00",
  "metrics_endpoint": "http://localhost:28284/metrics"
}
```

---

### `system.health`

Service health status. Returns `"healthy"` when fully running, `"starting"` during initialisation.

**Request** — no additional fields required.

**Response `data`**
```json
{
  "service": "moderator",
  "status": "healthy",
  "version": "0.7.0",
  "uptime_seconds": 3600.0
}
```

---

### `system.stats`

Full runtime statistics and counters.

**Request** — no additional fields required.

**Response `data`**
```json
{
  "service": "moderator",
  "version": "0.7.0",
  "uptime_seconds": 3600.0,
  "events_processed": 1234,
  "commands_processed": 56,
  "messages_checked": 1200,
  "messages_flagged": 3,
  "users_tracked": 89,
  "moderation_entries": 12,
  "moderation_lists": 1,
  "ip_mappings": 450,
  "ip_correlations": 2,
  "patterns": 4,
  "pattern_matches": 1,
  "bans_enforced": 3,
  "smutes_enforced": 5,
  "mutes_enforced": 2
}
```

---

### `entry.add`

Add a user to a moderation list. If the user is currently online the action is applied immediately; otherwise it fires on their next join.

**Request fields**

| Field | Type | Required | Description |
|---|---|---|---|
| `channel` | string | ✅ | CyTube channel name |
| `username` | string | ✅ | Target username |
| `action` | string | ✅ | `"ban"`, `"smute"`, or `"mute"` |
| `domain` | string | — | CyTube domain; defaults to first configured channel |
| `reason` | string | — | Human-readable reason (shown in logs) |
| `moderator` | string | — | Who issued the action; defaults to `"cli"` |

**Response `data`**
```json
{
  "username": "baduser",
  "action": "ban",
  "reason": "spamming",
  "moderator": "admin",
  "timestamp": "2026-07-04T00:00:00",
  "channel": "lounge",
  "domain": "cytu.be"
}
```

---

### `entry.remove`

Remove a user from a moderation list. If the user is currently online and was muted/smuted they will be unmuted.

**Request fields**

| Field | Type | Required | Description |
|---|---|---|---|
| `channel` | string | ✅ | CyTube channel name |
| `username` | string | ✅ | Target username |
| `domain` | string | — | CyTube domain |

**Response `data`**
```json
{
  "username": "baduser",
  "channel": "lounge",
  "domain": "cytu.be",
  "removed": true
}
```

Returns an error (`success: false`) if the user is not in the moderation list.

---

### `entry.get`

Retrieve the moderation entry for a user, or confirm they are not moderated.

**Request fields**

| Field | Type | Required | Description |
|---|---|---|---|
| `channel` | string | ✅ | CyTube channel name |
| `username` | string | ✅ | Username to look up |
| `domain` | string | — | CyTube domain |

**Response `data`** — not moderated
```json
{
  "username": "cleanuser",
  "channel": "lounge",
  "domain": "cytu.be",
  "moderated": false
}
```

**Response `data`** — moderated
```json
{
  "username": "baduser",
  "channel": "lounge",
  "domain": "cytu.be",
  "moderated": true,
  "action": "ban",
  "reason": "spamming",
  "moderator": "admin",
  "timestamp": "2026-07-04T00:00:00",
  "ips": ["1.2.3.x"],
  "ip_correlation_source": null,
  "pattern_match": null
}
```

`ip_correlation_source` is the username of the original moderated account when this entry was created by IP correlation. `pattern_match` is the pattern string that triggered automatic moderation.

---

### `entry.list`

List all moderation entries for a channel, optionally filtered by action type.

**Request fields**

| Field | Type | Required | Description |
|---|---|---|---|
| `channel` | string | ✅ | CyTube channel name |
| `domain` | string | — | CyTube domain |
| `filter` | string | — | Return only entries matching this action: `"ban"`, `"smute"`, or `"mute"` |

**Response `data`**
```json
{
  "channel": "lounge",
  "domain": "cytu.be",
  "count": 2,
  "entries": [
    {
      "username": "baduser",
      "action": "ban",
      "reason": "spamming",
      "moderator": "admin",
      "timestamp": "2026-07-04T00:00:00",
      "ip_correlation_source": null,
      "pattern_match": null
    }
  ]
}
```

---

### `pattern.add`

Register a banned username pattern. When a user joins whose username matches the pattern, the specified action is applied automatically.

**Request fields**

| Field | Type | Required | Description |
|---|---|---|---|
| `channel` | string | ✅ | CyTube channel name |
| `pattern` | string | ✅ | Pattern string or regular expression |
| `domain` | string | — | CyTube domain |
| `is_regex` | bool | — | Treat `pattern` as a regex; default `false` (substring match) |
| `action` | string | — | `"ban"`, `"smute"`, or `"mute"`; default `"ban"` |
| `description` | string | — | Human-readable label shown in logs |
| `added_by` | string | — | Who added the pattern; defaults to `"cli"` |

**Response `data`**
```json
{
  "pattern": "1488",
  "is_regex": false,
  "action": "ban",
  "added_by": "admin",
  "description": "Nazi hate symbol",
  "timestamp": "2026-07-04T00:00:00",
  "channel": "lounge",
  "domain": "cytu.be"
}
```

---

### `pattern.remove`

Remove a registered pattern by its exact string.

**Request fields**

| Field | Type | Required | Description |
|---|---|---|---|
| `channel` | string | ✅ | CyTube channel name |
| `pattern` | string | ✅ | Exact pattern string to remove |
| `domain` | string | — | CyTube domain |

**Response `data`**
```json
{
  "pattern": "1488",
  "channel": "lounge",
  "domain": "cytu.be",
  "removed": true
}
```

Returns an error if the pattern is not found.

---

### `pattern.list`

List all registered banned patterns for a channel.

**Request fields**

| Field | Type | Required | Description |
|---|---|---|---|
| `channel` | string | ✅ | CyTube channel name |
| `domain` | string | — | CyTube domain |

**Response `data`**
```json
{
  "channel": "lounge",
  "domain": "cytu.be",
  "count": 2,
  "patterns": [
    {
      "pattern": "1488",
      "is_regex": false,
      "action": "ban",
      "added_by": "admin",
      "description": "Nazi hate symbol",
      "timestamp": "2026-07-04T00:00:00"
    }
  ]
}
```

---

### `users.recent`

List users seen in a channel within a rolling time window, with per-session detail.
Designed for identifying "driveby" accounts: users who join briefly, act, and leave.

**Request fields**

| Field | Type | Required | Description |
|---|---|---|---|
| `channel` | string | ✅ | CyTube channel name |
| `domain` | string | — | CyTube domain |
| `window_minutes` | number | — | How far back to look; default `60`, capped at `history_retention_hours × 60` |

**Response `data`**
```json
{
  "channel": "lounge",
  "domain": "cytu.be",
  "window_minutes": 60.0,
  "generated_at": "2026-07-04T01:00:00+00:00",
  "count": 1,
  "users": [
    {
      "username": "driveby_user",
      "moderation_action": null,
      "session_count": 3,
      "total_messages": 2,
      "last_seen": "2026-07-04T00:59:00+00:00",
      "sessions": [
        {
          "joined_at": "2026-07-04T00:45:00+00:00",
          "left_at": "2026-07-04T00:45:12+00:00",
          "duration_seconds": 12.0,
          "ip": "1.2.3.x",
          "message_count": 1
        },
        {
          "joined_at": "2026-07-04T00:59:00+00:00",
          "left_at": null,
          "duration_seconds": null,
          "ip": "1.2.3.x",
          "message_count": 1
        }
      ]
    }
  ]
}
```

`moderation_action` is the user's **current** moderation status (`"ban"`, `"smute"`, `"mute"`, or `null`).
`left_at` is `null` and `duration_seconds` is `null` for sessions where the user is still present.
Results are sorted by `last_seen` descending (most recently active user first).

The service retains up to `history_retention_hours` (default 12) of data; configure under
`moderation.history_retention_hours` in `config.json`.

---

## Suggested REST Endpoint Mapping

Recommended mapping for kryten-api-gate. All endpoints operate on a named
`channel`; `domain` is passed as an optional query parameter when needed.

| Method | Path | NATS command |
|---|---|---|
| `GET` | `/api/v1/channels/{channel}/moderation` | `entry.list` |
| `POST` | `/api/v1/channels/{channel}/moderation` | `entry.add` |
| `GET` | `/api/v1/channels/{channel}/moderation/{username}` | `entry.get` |
| `DELETE` | `/api/v1/channels/{channel}/moderation/{username}` | `entry.remove` |
| `GET` | `/api/v1/channels/{channel}/patterns` | `pattern.list` |
| `POST` | `/api/v1/channels/{channel}/patterns` | `pattern.add` |
| `DELETE` | `/api/v1/channels/{channel}/patterns/{pattern}` | `pattern.remove` |
| `GET` | `/api/v1/channels/{channel}/users/recent` | `users.recent` |
| `GET` | `/api/v1/system/ping` | `system.ping` |
| `GET` | `/api/v1/system/health` | `system.health` |
| `GET` | `/api/v1/system/stats` | `system.stats` |

### Error → HTTP status mapping

| Condition | HTTP status |
|---|---|
| NATS request times out (no reply) | `503 Service Unavailable` |
| `success: false`, error contains "required" | `400 Bad Request` |
| `success: false`, error contains "not found" / "not in" | `404 Not Found` |
| `success: false`, error contains "must be" / "invalid" | `400 Bad Request` |
| `success: false`, any other error | `500 Internal Server Error` |
| `success: true` on `DELETE` | `200 OK` (return the `data` object) |
| `success: true` on `POST` | `201 Created` (return the `data` object) |
| `success: true` on `GET` | `200 OK` (return the `data` object) |

---

## Planned: Event Publishing (v0.8.0)

When kryten-api-gate requires real-time push notifications the moderator will
publish events to `kryten.moderator.event.<type>`. The hook method
`ModeratorCommandHandler._emit_event(event_type, payload)` is already present
in `nats_handler.py` as a documented no-op stub — activate it by replacing the
body with a `client.publish` call.

| Subject | Fires when |
|---|---|
| `kryten.moderator.event.enforcement.applied` | A moderation action is enforced on a joining user (direct list hit, IP correlation, or pattern match) |
| `kryten.moderator.event.enforcement.removed` | A user is unmuted / cleared from the moderation list |

Additional events to add as needed:

| Subject | Fires when |
|---|---|
| `kryten.moderator.event.entry.added` | A moderation entry is created via `entry.add` |
| `kryten.moderator.event.entry.removed` | A moderation entry is removed via `entry.remove` |
| `kryten.moderator.event.pattern.added` | A pattern is registered via `pattern.add` |
| `kryten.moderator.event.pattern.removed` | A pattern is removed via `pattern.remove` |

---

## Metrics

A Prometheus-compatible metrics endpoint is exposed at:

```
http://localhost:<metrics.port>/metrics
```

The port is configured in `config.json` under `metrics.port` (default `28284`).
The current endpoint is also returned in every `system.ping` response.
