# Phase 3: Username Pattern Matching

**Duration**: 2-3 days  
**PRD Reference**: Section 9.3, Phase 3  
**User Stories**: MOD-010, MOD-011  
**Depends On**: Phase 1 (Core Moderation List)

## Overview

Phase 3 implements automatic username pattern matching for instant moderation of users with offensive or known-bad usernames. This enables proactive blocking of hate symbols, slurs, and other problematic username patterns before users can cause harm.

## Deliverables

1. **Pattern Storage and Management** (MOD-011)
   - KV bucket: `kryten_moderator_patterns`
   - Pattern schema with regex support
   - Add/remove pattern operations
   - NATS commands for pattern management

2. **Pattern Matching Engine** (MOD-010)
   - Check usernames against all patterns on join
   - Support both substring and regex patterns
   - Case-insensitive matching
   - Compiled regex caching for performance

3. **Default Patterns Configuration**
   - Configurable default patterns in config.json
   - Auto-load on first startup
   - Common hate symbol patterns

4. **Metrics and Logging**
   - Counter: `moderator_pattern_matches`
   - Log pattern matches with matched pattern

---

## Technical Specification

### 1. Data Model

#### Pattern Entry Schema

```python
@dataclass
class PatternEntry:
    """A banned username pattern."""
    pattern: str           # The pattern string
    is_regex: bool        # True if regex, False if substring
    action: ActionType    # "ban" (default), "smute", or "mute"
    added_by: str         # Who added this pattern
    timestamp: str        # ISO 8601 timestamp
    description: str | None  # Optional description
```

#### KV Storage Format

**Bucket**: `kryten_moderator_patterns`  
**Key**: Pattern string (URL-safe encoded if special chars)  
**Value**: JSON-encoded PatternEntry

```json
{
  "pattern": "1488",
  "is_regex": false,
  "action": "ban",
  "added_by": "admin_user",
  "timestamp": "2025-12-14T10:00:00Z",
  "description": "Nazi hate number"
}
```

```json
{
  "pattern": "^.*hitler.*$",
  "is_regex": true,
  "action": "ban",
  "added_by": "admin_user",
  "timestamp": "2025-12-14T10:00:00Z",
  "description": "Hitler reference anywhere in name"
}
```

### 2. Pattern Manager Module

#### New Module: `pattern_manager.py`

```python
"""Username pattern matching for automatic moderation."""

import json
import logging
import re
import base64
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Literal

from kryten import KrytenClient

ActionType = Literal["ban", "smute", "mute"]

KV_BUCKET_PATTERNS = "kryten_moderator_patterns"


@dataclass
class PatternEntry:
    """A banned username pattern."""
    pattern: str
    is_regex: bool
    action: ActionType
    added_by: str
    timestamp: str
    description: str | None = None
    
    def to_json(self) -> str:
        return json.dumps(asdict(self))
    
    @classmethod
    def from_json(cls, data: str) -> "PatternEntry":
        return cls(**json.loads(data))


class CompiledPattern:
    """A pattern with compiled regex for fast matching."""
    
    def __init__(self, entry: PatternEntry):
        self.entry = entry
        self._regex: re.Pattern | None = None
        self._compile()
    
    def _compile(self) -> None:
        """Compile the pattern for matching."""
        if self.entry.is_regex:
            try:
                self._regex = re.compile(self.entry.pattern, re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern: {e}")
        else:
            # For substring, create a regex that matches anywhere
            escaped = re.escape(self.entry.pattern)
            self._regex = re.compile(escaped, re.IGNORECASE)
    
    def matches(self, username: str) -> bool:
        """Check if username matches this pattern."""
        if self._regex:
            return bool(self._regex.search(username))
        return False


class PatternManager:
    """Manages banned username patterns."""

    def __init__(self, client: KrytenClient):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self._kv = None
        self._patterns: dict[str, CompiledPattern] = {}

    async def initialize(self, default_patterns: list[dict] | None = None) -> None:
        """Initialize pattern bucket and load patterns.
        
        Args:
            default_patterns: Optional list of default patterns to add if bucket is empty
        """
        js = self.client.jetstream
        
        try:
            self._kv = await js.key_value(KV_BUCKET_PATTERNS)
            self.logger.info(f"Connected to KV bucket: {KV_BUCKET_PATTERNS}")
        except Exception:
            self._kv = await js.create_key_value(
                name=KV_BUCKET_PATTERNS,
                description="Banned username patterns",
                history=3,
            )
            self.logger.info(f"Created KV bucket: {KV_BUCKET_PATTERNS}")

        # Load existing patterns
        await self._load_patterns()
        
        # Add default patterns if bucket was empty
        if not self._patterns and default_patterns:
            await self._add_default_patterns(default_patterns)

    async def _load_patterns(self) -> None:
        """Load all patterns from KV into compiled cache."""
        self._patterns.clear()
        
        try:
            keys = await self._kv.keys()
            for key in keys:
                entry = await self._kv.get(key)
                if entry and entry.value:
                    pattern_entry = PatternEntry.from_json(entry.value.decode())
                    try:
                        self._patterns[key] = CompiledPattern(pattern_entry)
                    except ValueError as e:
                        self.logger.warning(f"Skipping invalid pattern '{key}': {e}")
                        
            self.logger.info(f"Loaded {len(self._patterns)} patterns into cache")
        except Exception as e:
            self.logger.warning(f"Could not load patterns: {e}")

    async def _add_default_patterns(self, patterns: list[dict]) -> None:
        """Add default patterns on first startup.
        
        Args:
            patterns: List of pattern configs from config.json
        """
        self.logger.info(f"Adding {len(patterns)} default patterns")
        
        for p in patterns:
            if isinstance(p, str):
                # Simple string pattern
                await self.add(
                    pattern=p,
                    is_regex=False,
                    action="ban",
                    added_by="system:default",
                    description="Default pattern",
                )
            elif isinstance(p, dict):
                # Full pattern config
                await self.add(
                    pattern=p["pattern"],
                    is_regex=p.get("is_regex", False),
                    action=p.get("action", "ban"),
                    added_by="system:default",
                    description=p.get("description"),
                )

    @staticmethod
    def _make_key(pattern: str) -> str:
        """Create a safe KV key from pattern string.
        
        Some patterns may contain special characters that aren't valid in keys.
        """
        # Use base64 encoding for safety
        return base64.urlsafe_b64encode(pattern.encode()).decode()

    async def add(
        self,
        pattern: str,
        is_regex: bool,
        action: ActionType,
        added_by: str,
        description: str | None = None,
    ) -> PatternEntry:
        """Add a pattern to the banned list.
        
        Args:
            pattern: Pattern string (substring or regex)
            is_regex: True if pattern is a regex
            action: Action to take on match (ban/smute/mute)
            added_by: Who is adding this pattern
            description: Optional description
            
        Returns:
            The created PatternEntry
            
        Raises:
            ValueError: If regex is invalid
        """
        entry = PatternEntry(
            pattern=pattern,
            is_regex=is_regex,
            action=action,
            added_by=added_by,
            timestamp=datetime.now(timezone.utc).isoformat(),
            description=description,
        )
        
        # Validate by compiling
        compiled = CompiledPattern(entry)
        
        # Store in KV
        key = self._make_key(pattern)
        await self._kv.put(key, entry.to_json().encode())
        
        # Update cache
        self._patterns[key] = compiled
        
        self.logger.info(f"Added pattern: '{pattern}' (regex={is_regex}, action={action})")
        return entry

    async def remove(self, pattern: str) -> bool:
        """Remove a pattern from the banned list.
        
        Args:
            pattern: Pattern string to remove
            
        Returns:
            True if removed, False if not found
        """
        key = self._make_key(pattern)
        
        if key not in self._patterns:
            return False
        
        await self._kv.delete(key)
        del self._patterns[key]
        
        self.logger.info(f"Removed pattern: '{pattern}'")
        return True

    async def list_all(self) -> list[PatternEntry]:
        """List all patterns.
        
        Returns:
            List of PatternEntry objects
        """
        return [cp.entry for cp in self._patterns.values()]

    def check_username(self, username: str) -> tuple[PatternEntry, str] | None:
        """Check if username matches any banned pattern.
        
        This is the hot path for join-time matching.
        
        Args:
            username: Username to check
            
        Returns:
            (PatternEntry, matched_pattern) if match found, None otherwise
        """
        for compiled in self._patterns.values():
            if compiled.matches(username):
                return (compiled.entry, compiled.entry.pattern)
        
        return None

    @property
    def count(self) -> int:
        """Return the number of patterns."""
        return len(self._patterns)
```

### 3. Service Integration

#### Updates to `service.py`

```python
from .pattern_manager import PatternManager

class ModeratorService:
    def __init__(self, config_path: str):
        # ... existing init ...
        
        # Pattern Manager
        self.pattern_manager: PatternManager | None = None
        
        # Statistics
        self._pattern_matches = 0
        
    async def start(self) -> None:
        # ... after ip_manager.initialize() ...
        
        # Initialize pattern manager if enabled
        mod_config = self.config.get("moderation", {})
        if mod_config.get("enable_pattern_matching", True):
            self.pattern_manager = PatternManager(self.client)
            
            # Get default patterns from config
            default_patterns = mod_config.get("default_patterns", [])
            
            await self.pattern_manager.initialize(default_patterns)
            self.logger.info(f"Pattern matching enabled with {self.pattern_manager.count} patterns")
        else:
            self.logger.info("Pattern matching disabled in config")
        
    async def _handle_user_join(self, event: UserJoinEvent) -> None:
        """Handle user join - check patterns first, then username, then IP."""
        self._events_processed += 1
        self._users_tracked.add(event.username.lower())
        
        try:
            # Check 1: Pattern matching (highest priority - instant action)
            if self.pattern_manager:
                match = self.pattern_manager.check_username(event.username)
                if match:
                    pattern_entry, matched_pattern = match
                    await self._handle_pattern_match(event, pattern_entry, matched_pattern)
                    return
            
            # Check 2: Direct username match in moderation list
            entry = self.moderation_list.check_username(event.username)
            if entry:
                # Store IP if available
                await self._maybe_store_ip(event, entry)
                await self._enforce_moderation(event, entry)
                return
            
            # Check 3: IP correlation (if enabled)
            if self.ip_manager:
                await self._check_ip_correlation(event)
                    
        except Exception as e:
            self.logger.error(f"Error handling user join: {e}", exc_info=True)

    async def _handle_pattern_match(
        self,
        event: UserJoinEvent,
        pattern_entry: PatternEntry,
        matched_pattern: str,
    ) -> None:
        """Handle username that matches a banned pattern."""
        self._pattern_matches += 1
        
        self.logger.warning(
            f"PATTERN MATCH: {event.username} matched pattern '{matched_pattern}' "
            f"(action: {pattern_entry.action})"
        )
        
        # Create moderation entry with pattern reference
        mod_entry = await self.moderation_list.add(
            username=event.username,
            action=pattern_entry.action,
            moderator="system:pattern_match",
            reason=f"Pattern match: {matched_pattern}",
        )
        
        # Update entry with pattern_match field
        mod_entry.pattern_match = matched_pattern
        await self.moderation_list.update(mod_entry)
        
        # Extract and store IP if available
        await self._maybe_store_ip(event, mod_entry)
        
        # Enforce the action
        await self._enforce_moderation(event, mod_entry)
```

### 4. NATS Command Handler Updates

Add pattern management commands:

```python
async def _handle_command(self, msg) -> dict:
    """Route incoming commands."""
    try:
        data = json.loads(msg.data.decode())
        command = data.get("command", "")
        
        # ... existing commands ...
        
        # Pattern commands
        elif command == "patterns.list":
            return await self._cmd_patterns_list()
        elif command == "patterns.add":
            return await self._cmd_patterns_add(data)
        elif command == "patterns.remove":
            return await self._cmd_patterns_remove(data)
        
        else:
            return {"success": False, "error": f"Unknown command: {command}"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}

async def _cmd_patterns_list(self) -> dict:
    """List all banned patterns."""
    if not self.service.pattern_manager:
        return {"success": False, "error": "Pattern matching is disabled"}
    
    patterns = await self.service.pattern_manager.list_all()
    
    return {
        "success": True,
        "data": {
            "count": len(patterns),
            "patterns": [
                {
                    "pattern": p.pattern,
                    "is_regex": p.is_regex,
                    "action": p.action,
                    "added_by": p.added_by,
                    "timestamp": p.timestamp,
                    "description": p.description,
                }
                for p in patterns
            ]
        }
    }

async def _cmd_patterns_add(self, data: dict) -> dict:
    """Add a banned pattern."""
    if not self.service.pattern_manager:
        return {"success": False, "error": "Pattern matching is disabled"}
    
    pattern = data.get("pattern")
    is_regex = data.get("is_regex", False)
    action = data.get("action", "ban")
    added_by = data.get("added_by", "cli")
    description = data.get("description")
    
    if not pattern:
        return {"success": False, "error": "pattern is required"}
    if action not in ("ban", "smute", "mute"):
        return {"success": False, "error": "action must be ban, smute, or mute"}
    
    try:
        entry = await self.service.pattern_manager.add(
            pattern=pattern,
            is_regex=is_regex,
            action=action,
            added_by=added_by,
            description=description,
        )
        
        self.service._commands_processed += 1
        
        return {
            "success": True,
            "data": {
                "pattern": entry.pattern,
                "is_regex": entry.is_regex,
                "action": entry.action,
                "added_by": entry.added_by,
            }
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}

async def _cmd_patterns_remove(self, data: dict) -> dict:
    """Remove a banned pattern."""
    if not self.service.pattern_manager:
        return {"success": False, "error": "Pattern matching is disabled"}
    
    pattern = data.get("pattern")
    
    if not pattern:
        return {"success": False, "error": "pattern is required"}
    
    removed = await self.service.pattern_manager.remove(pattern)
    
    if not removed:
        return {"success": False, "error": f"Pattern '{pattern}' not found"}
    
    self.service._commands_processed += 1
    
    return {"success": True, "data": {"pattern": pattern, "removed": True}}
```

### 5. Metrics Updates

```python
def _collect_custom_metrics(self) -> str:
    lines = []
    # ... existing metrics ...
    
    # Pattern matching metrics
    lines.append(f"moderator_pattern_matches {self.service._pattern_matches}")
    
    # Pattern count
    if self.service.pattern_manager:
        lines.append(f"moderator_pattern_count {self.service.pattern_manager.count}")
    
    return "\n".join(lines)
```

---

## Configuration

Add pattern settings to config.json:

```json
{
  "moderation": {
    "enable_pattern_matching": true,
    "default_patterns": [
      "1488",
      "14/88",
      {"pattern": "hitler", "is_regex": false, "action": "ban", "description": "Hitler reference"},
      {"pattern": "88$", "is_regex": true, "action": "ban", "description": "Ends with 88"},
      {"pattern": "^.*nazi.*$", "is_regex": true, "action": "ban", "description": "Contains nazi"}
    ]
  },
  "kv_buckets": {
    "patterns": "kryten_moderator_patterns"
  }
}
```

---

## NATS Commands

### Pattern Management Commands

| Command | Description | Required Fields |
|---------|-------------|-----------------|
| `patterns.list` | List all patterns | (none) |
| `patterns.add` | Add a pattern | `pattern` |
| `patterns.remove` | Remove a pattern | `pattern` |

### Example Requests

**List patterns:**
```json
{
  "service": "moderator",
  "command": "patterns.list"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "count": 3,
    "patterns": [
      {
        "pattern": "1488",
        "is_regex": false,
        "action": "ban",
        "added_by": "system:default",
        "timestamp": "2025-12-14T10:00:00Z",
        "description": "Nazi hate number"
      }
    ]
  }
}
```

**Add substring pattern:**
```json
{
  "service": "moderator",
  "command": "patterns.add",
  "pattern": "badword",
  "is_regex": false,
  "action": "ban",
  "added_by": "admin",
  "description": "Bad word substring"
}
```

**Add regex pattern:**
```json
{
  "service": "moderator",
  "command": "patterns.add",
  "pattern": "^troll\\d+$",
  "is_regex": true,
  "action": "smute",
  "added_by": "admin",
  "description": "Troll followed by numbers"
}
```

**Remove pattern:**
```json
{
  "service": "moderator",
  "command": "patterns.remove",
  "pattern": "badword"
}
```

---

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `kryten_moderator/pattern_manager.py` | **NEW** | PatternManager and CompiledPattern classes |
| `kryten_moderator/service.py` | **MODIFY** | Pattern checking in join handler |
| `kryten_moderator/nats_handler.py` | **MODIFY** | patterns.* command routing |
| `kryten_moderator/metrics_server.py` | **MODIFY** | Pattern metrics |
| `pyproject.toml` | **MODIFY** | Bump version to 0.5.0 |

---

## Testing Checklist

### Unit Tests

- [ ] PatternEntry serialization/deserialization
- [ ] CompiledPattern matches substring patterns (case-insensitive)
- [ ] CompiledPattern matches regex patterns
- [ ] Invalid regex raises ValueError on add
- [ ] PatternManager.add() stores in KV
- [ ] PatternManager.remove() deletes from KV
- [ ] PatternManager.check_username() returns first match
- [ ] Default patterns are loaded on first startup
- [ ] Base64 key encoding handles special characters

### Integration Tests

- [ ] Pattern bucket is created on startup
- [ ] Default patterns from config are added
- [ ] `patterns.list` returns all patterns
- [ ] `patterns.add` creates pattern
- [ ] `patterns.add` rejects invalid regex
- [ ] `patterns.remove` removes pattern
- [ ] User join with matching name triggers action
- [ ] Pattern match creates moderation entry with pattern_match field
- [ ] Metrics include pattern match counter

### Manual Testing

- [ ] Add pattern "test", join as "TestUser123", verify action taken
- [ ] Add regex "^evil.*", join as "EvilBot", verify match
- [ ] Verify case-insensitive matching works
- [ ] Remove pattern, verify no longer matches

---

## Common Patterns Reference

Default patterns that cover common hate symbols and problematic usernames:

| Pattern | Type | Description |
|---------|------|-------------|
| `1488` | substring | White supremacist number |
| `14/88` | substring | White supremacist number variant |
| `88$` | regex | Ends with 88 (common neo-Nazi suffix) |
| `hitler` | substring | Hitler reference |
| `nazi` | substring | Nazi reference |
| `heil` | substring | Nazi greeting |
| `sieg` | substring | Often part of Nazi phrases |
| `卐` | substring | Swastika character |
| `卍` | substring | Reverse swastika |

---

## Edge Cases

1. **Empty pattern**: Reject - would match everything
2. **Very broad regex**: Warn but allow (`.+` matches all)
3. **Overlapping patterns**: First match wins (order undefined)
4. **Unicode patterns**: Support Unicode characters in patterns
5. **Special chars in pattern**: URL-safe base64 encoding for keys

---

## Performance Considerations

- Patterns are compiled once and cached
- Join-time matching iterates all patterns (O(n) where n = pattern count)
- For large pattern sets (100+), consider:
  - Aho-Corasick algorithm for substring matching
  - Pattern categorization and early exit

---

## Dependencies

- Phase 1 complete (ModerationList functional)
- `kryten-py >= 0.9.4`
- NATS JetStream enabled

---

## Version

After completing Phase 3: `kryten-moderator v0.5.0`
