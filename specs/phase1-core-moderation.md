# Phase 1: Core Moderation List and Enforcement

**Duration**: 3-4 days  
**PRD Reference**: Section 9.3, Phase 1  
**User Stories**: MOD-001, MOD-002, MOD-003, MOD-004, MOD-004a, MOD-004b, MOD-007, MOD-008, MOD-008a, MOD-013

## Overview

Phase 1 implements the core moderation list storage, basic CRUD operations, and automatic enforcement on user join. This phase establishes the foundational data model and enforcement pipeline that all subsequent phases build upon.

## Deliverables

1. **KV Store Schema and Persistence** (MOD-013)
   - NATS JetStream KV bucket: `kryten_moderator_entries`
   - Automatic bucket creation on startup
   - Entry schema with action types: ban, smute, mute

2. **Moderation Entry CRUD Operations** (MOD-001 through MOD-004b)
   - Add entry (ban/smute/mute)
   - Remove entry (unban/unsmute/unmute)
   - Get entry by username
   - List all entries with optional filter

3. **Automatic Enforcement on Join** (MOD-007, MOD-008, MOD-008a)
   - Check username against moderation list on `adduser` event
   - Execute appropriate action: kick (ban), `/smute`, or `/mute`
   - Log enforcement actions
   - Increment metrics counters

4. **NATS Command Interface**
   - Subject: `kryten.moderator.command`
   - Commands: `entry.add`, `entry.remove`, `entry.get`, `entry.list`

---

## Technical Specification

### 1. Data Model

#### Moderation Entry Schema

```python
@dataclass
class ModerationEntry:
    """A moderation list entry."""
    username: str           # Lowercased for matching
    action: str            # "ban" | "smute" | "mute"
    reason: str | None     # Optional reason
    moderator: str         # Who added this entry
    timestamp: str         # ISO 8601 timestamp
    ips: list[str]         # Associated IPs (Phase 2)
    ip_correlation_source: str | None  # Username this was correlated from (Phase 2)
    pattern_match: str | None          # Pattern that matched (Phase 3)
```

#### KV Storage Format

**Bucket**: `kryten_moderator_entries`  
**Key**: Lowercased username (e.g., `trolluser`)  
**Value**: JSON-encoded ModerationEntry

```json
{
  "username": "trolluser",
  "action": "smute",
  "reason": "Disruptive behavior",
  "moderator": "admin_user",
  "timestamp": "2025-12-14T10:30:00Z",
  "ips": [],
  "ip_correlation_source": null,
  "pattern_match": null
}
```

### 2. New Module: `moderation_list.py`

```python
"""Moderation list management using NATS KV store."""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Literal

from kryten import KrytenClient

ActionType = Literal["ban", "smute", "mute"]

KV_BUCKET_ENTRIES = "kryten_moderator_entries"


@dataclass
class ModerationEntry:
    """A moderation list entry."""
    username: str
    action: ActionType
    reason: str | None
    moderator: str
    timestamp: str
    ips: list[str]
    ip_correlation_source: str | None = None
    pattern_match: str | None = None

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "ModerationEntry":
        """Deserialize from JSON string."""
        return cls(**json.loads(data))


class ModerationList:
    """Manages the moderation list in NATS KV store."""

    def __init__(self, client: KrytenClient):
        """Initialize with a connected KrytenClient."""
        self.client = client
        self.logger = logging.getLogger(__name__)
        self._kv = None
        self._cache: dict[str, ModerationEntry] = {}

    async def initialize(self) -> None:
        """Initialize KV bucket, create if not exists."""
        js = self.client.jetstream
        
        # Try to get existing bucket or create new one
        try:
            self._kv = await js.key_value(KV_BUCKET_ENTRIES)
            self.logger.info(f"Connected to KV bucket: {KV_BUCKET_ENTRIES}")
        except Exception:
            # Bucket doesn't exist, create it
            self._kv = await js.create_key_value(
                name=KV_BUCKET_ENTRIES,
                description="Kryten Moderator moderation entries",
                history=5,  # Keep some history for audit
            )
            self.logger.info(f"Created KV bucket: {KV_BUCKET_ENTRIES}")

        # Load all entries into cache for fast lookups
        await self._load_cache()

    async def _load_cache(self) -> None:
        """Load all entries from KV into memory cache."""
        self._cache.clear()
        
        try:
            keys = await self._kv.keys()
            for key in keys:
                entry = await self._kv.get(key)
                if entry and entry.value:
                    self._cache[key] = ModerationEntry.from_json(
                        entry.value.decode()
                    )
            self.logger.info(f"Loaded {len(self._cache)} moderation entries into cache")
        except Exception as e:
            self.logger.warning(f"Could not load cache (bucket may be empty): {e}")

    async def add(
        self,
        username: str,
        action: ActionType,
        moderator: str,
        reason: str | None = None,
    ) -> ModerationEntry:
        """Add a user to the moderation list.
        
        Args:
            username: Username to moderate (case-insensitive)
            action: "ban", "smute", or "mute"
            moderator: Who is adding this entry
            reason: Optional reason for the action
            
        Returns:
            The created ModerationEntry
        """
        key = username.lower()
        
        entry = ModerationEntry(
            username=username,
            action=action,
            reason=reason,
            moderator=moderator,
            timestamp=datetime.now(timezone.utc).isoformat(),
            ips=[],
        )
        
        # Store in KV
        await self._kv.put(key, entry.to_json().encode())
        
        # Update cache
        self._cache[key] = entry
        
        self.logger.info(f"Added moderation entry: {action} {username} by {moderator}")
        return entry

    async def remove(self, username: str) -> bool:
        """Remove a user from the moderation list.
        
        Args:
            username: Username to remove (case-insensitive)
            
        Returns:
            True if removed, False if not found
        """
        key = username.lower()
        
        if key not in self._cache:
            return False
        
        # Delete from KV
        await self._kv.delete(key)
        
        # Update cache
        del self._cache[key]
        
        self.logger.info(f"Removed moderation entry for: {username}")
        return True

    async def get(self, username: str) -> ModerationEntry | None:
        """Get moderation entry for a user.
        
        Args:
            username: Username to look up (case-insensitive)
            
        Returns:
            ModerationEntry if found, None otherwise
        """
        key = username.lower()
        return self._cache.get(key)

    async def list_all(
        self,
        filter_action: ActionType | None = None,
    ) -> list[ModerationEntry]:
        """List all moderation entries.
        
        Args:
            filter_action: Optional filter by action type
            
        Returns:
            List of ModerationEntry objects
        """
        entries = list(self._cache.values())
        
        if filter_action:
            entries = [e for e in entries if e.action == filter_action]
        
        # Sort by timestamp descending (most recent first)
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        
        return entries

    def check_username(self, username: str) -> ModerationEntry | None:
        """Check if username is in moderation list (fast, cache-only).
        
        This is the hot path for join enforcement.
        
        Args:
            username: Username to check (case-insensitive)
            
        Returns:
            ModerationEntry if found, None otherwise
        """
        return self._cache.get(username.lower())

    @property
    def size(self) -> int:
        """Return the number of entries in the moderation list."""
        return len(self._cache)
```

### 3. Service Integration

#### Updates to `service.py`

```python
# Add imports
from .moderation_list import ModerationList, ModerationEntry

class ModeratorService:
    def __init__(self, config_path: str):
        # ... existing init ...
        
        # Moderation list
        self.moderation_list: ModerationList | None = None
        
        # Statistics counters - add new ones
        self._bans_enforced = 0
        self._smutes_enforced = 0
        self._mutes_enforced = 0
        
    async def start(self) -> None:
        # ... after client.connect() ...
        
        # Initialize moderation list
        self.moderation_list = ModerationList(self.client)
        await self.moderation_list.initialize()
        self.logger.info(f"Moderation list initialized with {self.moderation_list.size} entries")
        
    async def _handle_user_join(self, event: UserJoinEvent) -> None:
        """Handle user join event - enforce moderation."""
        self._events_processed += 1
        self._users_tracked.add(event.username.lower())
        
        try:
            # Check moderation list
            entry = self.moderation_list.check_username(event.username)
            
            if entry:
                await self._enforce_moderation(event, entry)
                
        except Exception as e:
            self.logger.error(f"Error handling user join: {e}", exc_info=True)
    
    async def _enforce_moderation(
        self,
        event: UserJoinEvent,
        entry: ModerationEntry,
    ) -> None:
        """Enforce moderation action on joining user."""
        channel = self.config["channels"][0]["channel"]
        username = event.username
        
        self.logger.info(
            f"Enforcing {entry.action} on {username} "
            f"(reason: {entry.reason or 'N/A'})"
        )
        
        if entry.action == "ban":
            # Kick the user
            await self.client.kick_user(channel, username, reason=entry.reason)
            self._bans_enforced += 1
            self.logger.warning(f"ENFORCED BAN: Kicked {username}")
            
        elif entry.action == "smute":
            # Shadow mute - user doesn't know
            await self.client.shadow_mute_user(channel, username)
            self._smutes_enforced += 1
            self.logger.info(f"ENFORCED SMUTE: Shadow muted {username}")
            
        elif entry.action == "mute":
            # Visible mute - user is notified
            await self.client.mute_user(channel, username)
            self._mutes_enforced += 1
            self.logger.info(f"ENFORCED MUTE: Muted {username}")
```

### 4. NATS Command Handler Updates

#### Updates to `nats_handler.py`

Add command routing for moderation operations:

```python
async def _handle_command(self, msg) -> dict:
    """Route incoming commands."""
    try:
        data = json.loads(msg.data.decode())
        command = data.get("command", "")
        
        if command == "system.health":
            return await self._cmd_health()
        elif command == "system.stats":
            return await self._cmd_stats()
        
        # Moderation commands
        elif command == "entry.add":
            return await self._cmd_entry_add(data)
        elif command == "entry.remove":
            return await self._cmd_entry_remove(data)
        elif command == "entry.get":
            return await self._cmd_entry_get(data)
        elif command == "entry.list":
            return await self._cmd_entry_list(data)
        
        else:
            return {"success": False, "error": f"Unknown command: {command}"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}

async def _cmd_entry_add(self, data: dict) -> dict:
    """Add moderation entry."""
    username = data.get("username")
    action = data.get("action")  # "ban", "smute", "mute"
    reason = data.get("reason")
    moderator = data.get("moderator", "cli")
    
    if not username:
        return {"success": False, "error": "username is required"}
    if action not in ("ban", "smute", "mute"):
        return {"success": False, "error": "action must be ban, smute, or mute"}
    
    entry = await self.service.moderation_list.add(
        username=username,
        action=action,
        moderator=moderator,
        reason=reason,
    )
    
    # If user is online, apply action immediately
    await self._apply_action_if_online(username, entry)
    
    self.service._commands_processed += 1
    
    return {
        "success": True,
        "data": {
            "username": entry.username,
            "action": entry.action,
            "reason": entry.reason,
            "moderator": entry.moderator,
            "timestamp": entry.timestamp,
        }
    }

async def _cmd_entry_remove(self, data: dict) -> dict:
    """Remove moderation entry."""
    username = data.get("username")
    
    if not username:
        return {"success": False, "error": "username is required"}
    
    removed = await self.service.moderation_list.remove(username)
    
    if not removed:
        return {"success": False, "error": f"User '{username}' not in moderation list"}
    
    # If user is online, unmute them
    await self._unmute_if_online(username)
    
    self.service._commands_processed += 1
    
    return {"success": True, "data": {"username": username, "removed": True}}

async def _cmd_entry_get(self, data: dict) -> dict:
    """Get moderation entry for user."""
    username = data.get("username")
    
    if not username:
        return {"success": False, "error": "username is required"}
    
    entry = await self.service.moderation_list.get(username)
    
    if not entry:
        return {
            "success": True,
            "data": {"username": username, "moderated": False}
        }
    
    return {
        "success": True,
        "data": {
            "username": entry.username,
            "moderated": True,
            "action": entry.action,
            "reason": entry.reason,
            "moderator": entry.moderator,
            "timestamp": entry.timestamp,
            "ips": entry.ips,
        }
    }

async def _cmd_entry_list(self, data: dict) -> dict:
    """List all moderation entries."""
    filter_action = data.get("filter")  # "ban", "smute", "mute", or None
    
    entries = await self.service.moderation_list.list_all(filter_action=filter_action)
    
    return {
        "success": True,
        "data": {
            "count": len(entries),
            "entries": [
                {
                    "username": e.username,
                    "action": e.action,
                    "reason": e.reason,
                    "moderator": e.moderator,
                    "timestamp": e.timestamp,
                }
                for e in entries
            ]
        }
    }

async def _apply_action_if_online(self, username: str, entry: ModerationEntry) -> None:
    """Apply moderation action if user is currently online."""
    # TODO: Check userlist to see if user is online
    # For now, always attempt the action (will fail silently if not online)
    channel = self.service.config["channels"][0]["channel"]
    
    if entry.action == "ban":
        await self.service.client.kick_user(channel, username, reason=entry.reason)
    elif entry.action == "smute":
        await self.service.client.shadow_mute_user(channel, username)
    elif entry.action == "mute":
        await self.service.client.mute_user(channel, username)

async def _unmute_if_online(self, username: str) -> None:
    """Unmute user if currently online."""
    channel = self.service.config["channels"][0]["channel"]
    await self.service.client.unmute_user(channel, username)
```

### 5. Metrics Updates

#### Updates to `metrics_server.py`

Add new metrics for enforcement:

```python
def _collect_custom_metrics(self) -> str:
    """Collect moderator-specific metrics."""
    lines = []
    
    # Existing metrics
    lines.append(f"moderator_events_processed {self.service._events_processed}")
    lines.append(f"moderator_commands_processed {self.service._commands_processed}")
    lines.append(f"moderator_messages_checked {self.service._messages_checked}")
    lines.append(f"moderator_messages_flagged {self.service._messages_flagged}")
    lines.append(f"moderator_users_tracked {len(self.service._users_tracked)}")
    
    # New enforcement metrics
    lines.append(f"moderator_bans_enforced {self.service._bans_enforced}")
    lines.append(f"moderator_smutes_enforced {self.service._smutes_enforced}")
    lines.append(f"moderator_mutes_enforced {self.service._mutes_enforced}")
    
    # Moderation list size
    if self.service.moderation_list:
        lines.append(f"moderator_list_size {self.service.moderation_list.size}")
    
    return "\n".join(lines)
```

---

## NATS Command Interface

### Request Format

All commands sent to `kryten.moderator.command`:

```json
{
  "service": "moderator",
  "command": "<command_name>",
  ...additional fields...
}
```

### Commands

| Command | Description | Required Fields |
|---------|-------------|-----------------|
| `entry.add` | Add moderation entry | `username`, `action` |
| `entry.remove` | Remove moderation entry | `username` |
| `entry.get` | Get entry for user | `username` |
| `entry.list` | List all entries | (none) |

### Example Requests

**Add ban entry:**
```json
{
  "service": "moderator",
  "command": "entry.add",
  "username": "TrollUser",
  "action": "ban",
  "reason": "Harassment",
  "moderator": "admin"
}
```

**Add shadow mute entry:**
```json
{
  "service": "moderator",
  "command": "entry.add",
  "username": "SubtleTroll",
  "action": "smute",
  "reason": "Passive-aggressive behavior",
  "moderator": "mod1"
}
```

**Remove entry (unban/unsmute/unmute):**
```json
{
  "service": "moderator",
  "command": "entry.remove",
  "username": "ReformedUser"
}
```

**Check user:**
```json
{
  "service": "moderator",
  "command": "entry.get",
  "username": "SomeUser"
}
```

**List all entries:**
```json
{
  "service": "moderator",
  "command": "entry.list"
}
```

**List only bans:**
```json
{
  "service": "moderator",
  "command": "entry.list",
  "filter": "ban"
}
```

---

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `kryten_moderator/moderation_list.py` | **NEW** | ModerationList class with KV store operations |
| `kryten_moderator/service.py` | **MODIFY** | Add moderation list init, enforcement on join |
| `kryten_moderator/nats_handler.py` | **MODIFY** | Add entry.* command routing |
| `kryten_moderator/metrics_server.py` | **MODIFY** | Add enforcement metrics |
| `pyproject.toml` | **MODIFY** | Bump version to 0.3.0 |

---

## Testing Checklist

### Unit Tests

- [ ] ModerationList.add() creates entry in KV
- [ ] ModerationList.remove() deletes entry from KV
- [ ] ModerationList.get() returns entry or None
- [ ] ModerationList.list_all() returns all entries
- [ ] ModerationList.list_all(filter_action="ban") filters correctly
- [ ] ModerationList.check_username() uses cache (fast path)
- [ ] Entry serialization/deserialization round-trips correctly

### Integration Tests

- [ ] KV bucket is created on first startup
- [ ] Entries persist across service restart
- [ ] `entry.add` command creates entry and applies action
- [ ] `entry.remove` command removes entry and unmutes user
- [ ] `entry.get` returns correct status
- [ ] `entry.list` returns all entries with correct format
- [ ] User join triggers enforcement check
- [ ] Ban enforcement kicks user
- [ ] Smute enforcement shadow mutes user
- [ ] Mute enforcement visible mutes user

### Manual Testing

- [ ] Add ban entry via NATS command, verify user is kicked on join
- [ ] Add smute entry, verify user can chat but messages only visible to mods
- [ ] Add mute entry, verify user is notified they are muted
- [ ] Remove entry, verify user can participate normally
- [ ] Verify moderation list persists after service restart

---

## Dependencies

- `kryten-py >= 0.9.4` (KV store helpers, lifecycle events)
- NATS JetStream enabled

---

## Version

After completing Phase 1: `kryten-moderator v0.3.0`
