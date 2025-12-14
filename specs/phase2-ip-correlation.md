# Phase 2: IP Correlation for Ban Evasion Detection

**Duration**: 2-3 days  
**PRD Reference**: Section 9.3, Phase 2  
**User Stories**: MOD-009, MOD-012  
**Depends On**: Phase 1 (Core Moderation List)

## Overview

Phase 2 adds IP-based ban evasion detection. When a user joins, their IP is checked against IPs stored in existing moderation entries. If a match is found, the same moderation action is automatically applied to the new account, effectively blocking ban/smute evasion through alternate accounts.

## Deliverables

1. **IP Extraction from User Events** (MOD-012)
   - Extract IP from `adduser` event metadata
   - Handle both full IP and masked IP formats
   - Store IP with moderation entries when action is applied

2. **IP Storage in Moderation Entries** (MOD-012)
   - Extend entries with IP list
   - Store multiple IPs per user (VPN/dynamic IP support)
   - KV bucket for reverse IP lookups: `kryten_moderator_ip_map`

3. **IP Correlation Detection** (MOD-009)
   - Check joining user's IP against all stored IPs
   - Apply same action to new account if match found
   - Link new entry to original entry for audit trail
   - Log IP correlation events

4. **Metrics and Logging**
   - Counter: `moderator_ip_correlations`
   - Detailed logging with masked IPs for privacy

---

## Technical Specification

### 1. IP Address Handling

#### CyTube IP Format

CyTube provides IP addresses in user metadata. IPs may be:
- **Full IP**: `192.168.1.42` (when visible to moderators)
- **Masked IP**: `192.168.1.x` (partially hidden for privacy)
- **Unavailable**: Some server configurations hide IPs entirely

#### IP Extraction from Events

The `adduser` event from Kryten-Robot includes user metadata. We need to check if IP information is available in the event payload:

```python
# In UserJoinEvent (from kryten-py)
# event.raw contains the full event data from CyTube

def extract_ip_from_event(event: UserJoinEvent) -> tuple[str | None, str | None]:
    """Extract IP from user join event.
    
    Returns:
        Tuple of (full_ip, masked_ip), either may be None
    """
    raw = getattr(event, 'raw', {})
    meta = raw.get('meta', {})
    
    # CyTube stores IP in meta.ip or meta.aliases
    full_ip = meta.get('ip')
    masked_ip = meta.get('ip_masked') or meta.get('aliases', [None])[0]
    
    return full_ip, masked_ip
```

### 2. Data Model Updates

#### IP Map Schema

A reverse lookup map for efficient IP-to-username queries:

**Bucket**: `kryten_moderator_ip_map`  
**Key**: IP address (full or masked)  
**Value**: JSON array of usernames

```json
["trolluser", "trolluser_alt1", "trolluser_alt2"]
```

#### Extended ModerationEntry

```python
@dataclass
class ModerationEntry:
    username: str
    action: ActionType
    reason: str | None
    moderator: str
    timestamp: str
    ips: list[str]                      # List of associated IPs
    masked_ips: list[str]               # List of masked IPs for partial matching
    ip_correlation_source: str | None   # Original username if this was auto-added
    pattern_match: str | None           # Pattern that matched (Phase 3)
```

### 3. IP Manager Module

#### New Module: `ip_manager.py`

```python
"""IP correlation management for ban evasion detection."""

import json
import logging
from kryten import KrytenClient

KV_BUCKET_IP_MAP = "kryten_moderator_ip_map"


class IPManager:
    """Manages IP-to-username mapping for ban evasion detection."""

    def __init__(self, client: KrytenClient):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self._kv = None
        self._cache: dict[str, list[str]] = {}  # IP -> [usernames]

    async def initialize(self) -> None:
        """Initialize IP map KV bucket."""
        js = self.client.jetstream
        
        try:
            self._kv = await js.key_value(KV_BUCKET_IP_MAP)
            self.logger.info(f"Connected to KV bucket: {KV_BUCKET_IP_MAP}")
        except Exception:
            self._kv = await js.create_key_value(
                name=KV_BUCKET_IP_MAP,
                description="IP to username mapping for ban evasion detection",
                history=3,
            )
            self.logger.info(f"Created KV bucket: {KV_BUCKET_IP_MAP}")

        await self._load_cache()

    async def _load_cache(self) -> None:
        """Load IP map into memory cache."""
        self._cache.clear()
        
        try:
            keys = await self._kv.keys()
            for key in keys:
                entry = await self._kv.get(key)
                if entry and entry.value:
                    self._cache[key] = json.loads(entry.value.decode())
            self.logger.info(f"Loaded {len(self._cache)} IP mappings into cache")
        except Exception as e:
            self.logger.warning(f"Could not load IP cache: {e}")

    async def add_ip(self, ip: str, username: str) -> None:
        """Associate an IP with a username.
        
        Args:
            ip: IP address (full or masked)
            username: Username to associate
        """
        if not ip:
            return
            
        key = ip.lower()
        username_lower = username.lower()
        
        # Get or create list
        usernames = self._cache.get(key, [])
        
        # Add if not already present
        if username_lower not in usernames:
            usernames.append(username_lower)
            
            # Store in KV
            await self._kv.put(key, json.dumps(usernames).encode())
            
            # Update cache
            self._cache[key] = usernames
            
            self.logger.debug(f"Associated IP {self._mask_ip(ip)} with user {username}")

    async def remove_ip(self, ip: str, username: str) -> None:
        """Remove IP association for a username.
        
        Args:
            ip: IP address
            username: Username to disassociate
        """
        if not ip:
            return
            
        key = ip.lower()
        username_lower = username.lower()
        
        usernames = self._cache.get(key, [])
        
        if username_lower in usernames:
            usernames.remove(username_lower)
            
            if usernames:
                await self._kv.put(key, json.dumps(usernames).encode())
            else:
                await self._kv.delete(key)
                
            self._cache[key] = usernames
            
            self.logger.debug(f"Removed IP {self._mask_ip(ip)} association for {username}")

    def find_moderated_users_by_ip(
        self,
        ip: str,
        moderation_list: "ModerationList",
    ) -> list[tuple[str, "ModerationEntry"]]:
        """Find moderated users associated with an IP.
        
        Args:
            ip: IP address to check
            moderation_list: ModerationList to check against
            
        Returns:
            List of (username, entry) tuples for moderated users with this IP
        """
        if not ip:
            return []
            
        key = ip.lower()
        usernames = self._cache.get(key, [])
        
        results = []
        for username in usernames:
            entry = moderation_list.check_username(username)
            if entry:
                results.append((username, entry))
                
        return results

    def check_ip_correlation(
        self,
        ip: str,
        moderation_list: "ModerationList",
        exclude_username: str | None = None,
    ) -> tuple[str, "ModerationEntry"] | None:
        """Check if IP matches any moderated user.
        
        This is the hot path for join-time correlation.
        
        Args:
            ip: IP address to check
            moderation_list: ModerationList to check against
            exclude_username: Username to exclude (self-match prevention)
            
        Returns:
            (source_username, entry) if match found, None otherwise
        """
        if not ip:
            return None
            
        key = ip.lower()
        usernames = self._cache.get(key, [])
        
        exclude_lower = exclude_username.lower() if exclude_username else None
        
        for username in usernames:
            if username == exclude_lower:
                continue
                
            entry = moderation_list.check_username(username)
            if entry:
                return (username, entry)
                
        return None

    @staticmethod
    def _mask_ip(ip: str) -> str:
        """Mask IP for privacy in logs.
        
        Args:
            ip: Full or partial IP
            
        Returns:
            Masked IP (e.g., "192.168.x.x")
        """
        if not ip:
            return "(no IP)"
            
        parts = ip.split('.')
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[1]}.x.x"
        return ip
```

### 4. Service Integration

#### Updates to `service.py`

```python
from .ip_manager import IPManager

class ModeratorService:
    def __init__(self, config_path: str):
        # ... existing init ...
        
        # IP Manager
        self.ip_manager: IPManager | None = None
        
        # Statistics
        self._ip_correlations = 0
        
    async def start(self) -> None:
        # ... after moderation_list.initialize() ...
        
        # Check if IP correlation is enabled
        mod_config = self.config.get("moderation", {})
        if mod_config.get("enable_ip_correlation", True):
            self.ip_manager = IPManager(self.client)
            await self.ip_manager.initialize()
            self.logger.info("IP correlation enabled")
        else:
            self.logger.info("IP correlation disabled in config")
        
    async def _handle_user_join(self, event: UserJoinEvent) -> None:
        """Handle user join event - enforce moderation with IP correlation."""
        self._events_processed += 1
        self._users_tracked.add(event.username.lower())
        
        try:
            # Extract IP from event
            full_ip, masked_ip = self._extract_ip(event)
            ip = full_ip or masked_ip  # Prefer full IP
            
            # First check: direct username match
            entry = self.moderation_list.check_username(event.username)
            
            if entry:
                # Store IP with this entry if we have one
                if ip and ip not in entry.ips:
                    await self._add_ip_to_entry(event.username, ip)
                    
                await self._enforce_moderation(event, entry)
                return
            
            # Second check: IP correlation (if enabled)
            if self.ip_manager and ip:
                match = self.ip_manager.check_ip_correlation(
                    ip,
                    self.moderation_list,
                    exclude_username=event.username,
                )
                
                if match:
                    source_username, source_entry = match
                    await self._handle_ip_correlation(
                        event,
                        ip,
                        source_username,
                        source_entry,
                    )
                    
        except Exception as e:
            self.logger.error(f"Error handling user join: {e}", exc_info=True)

    async def _handle_ip_correlation(
        self,
        event: UserJoinEvent,
        ip: str,
        source_username: str,
        source_entry: ModerationEntry,
    ) -> None:
        """Handle detected IP correlation - apply same action to new account."""
        self._ip_correlations += 1
        
        masked_ip = IPManager._mask_ip(ip)
        self.logger.warning(
            f"IP CORRELATION DETECTED: {event.username} matches {source_username} "
            f"(IP: {masked_ip}, action: {source_entry.action})"
        )
        
        # Create new moderation entry linked to source
        new_entry = await self.moderation_list.add(
            username=event.username,
            action=source_entry.action,
            moderator="system:ip_correlation",
            reason=f"IP correlation with {source_username}: {source_entry.reason or 'N/A'}",
        )
        
        # Add IP to the new entry
        await self._add_ip_to_entry(event.username, ip)
        
        # Enforce the action
        await self._enforce_moderation(event, new_entry)

    async def _add_ip_to_entry(self, username: str, ip: str) -> None:
        """Add IP to user's moderation entry and IP map."""
        entry = await self.moderation_list.get(username)
        if entry and ip not in entry.ips:
            entry.ips.append(ip)
            # Update the entry in KV
            await self.moderation_list.add(
                username=entry.username,
                action=entry.action,
                moderator=entry.moderator,
                reason=entry.reason,
                # Note: This overwrites, need to implement update method
            )
            
        # Add to IP map
        if self.ip_manager:
            await self.ip_manager.add_ip(ip, username)

    def _extract_ip(self, event: UserJoinEvent) -> tuple[str | None, str | None]:
        """Extract IP from user join event."""
        raw = getattr(event, 'raw', {})
        meta = raw.get('meta', {})
        
        full_ip = meta.get('ip')
        masked_ip = meta.get('ip_masked')
        
        return full_ip, masked_ip
```

### 5. ModerationList Updates

Add an update method to modify existing entries:

```python
async def update(self, entry: ModerationEntry) -> None:
    """Update an existing moderation entry.
    
    Args:
        entry: Updated entry (must have same username)
    """
    key = entry.username.lower()
    
    if key not in self._cache:
        raise ValueError(f"Entry for {entry.username} not found")
    
    # Store in KV
    await self._kv.put(key, entry.to_json().encode())
    
    # Update cache
    self._cache[key] = entry
    
    self.logger.debug(f"Updated moderation entry for: {entry.username}")

async def add_ip_to_entry(self, username: str, ip: str) -> bool:
    """Add IP to existing entry.
    
    Args:
        username: Username to update
        ip: IP to add
        
    Returns:
        True if added, False if entry not found or IP already present
    """
    key = username.lower()
    entry = self._cache.get(key)
    
    if not entry:
        return False
        
    if ip in entry.ips:
        return False
        
    entry.ips.append(ip)
    await self.update(entry)
    
    return True
```

### 6. Metrics Updates

Add IP correlation counter to metrics:

```python
def _collect_custom_metrics(self) -> str:
    lines = []
    # ... existing metrics ...
    
    # IP correlation metrics
    lines.append(f"moderator_ip_correlations {self.service._ip_correlations}")
    
    # IP map size
    if self.service.ip_manager:
        lines.append(f"moderator_ip_map_size {len(self.service.ip_manager._cache)}")
    
    return "\n".join(lines)
```

---

## Configuration

Add IP correlation settings to config.json:

```json
{
  "moderation": {
    "enable_ip_correlation": true,
    "ip_correlation_log_masked": true
  },
  "kv_buckets": {
    "entries": "kryten_moderator_entries",
    "ip_map": "kryten_moderator_ip_map"
  }
}
```

---

## NATS Command Updates

No new NATS commands in Phase 2. IP correlation is automatic on user join.

The `entry.get` command will include IPs in response:

```json
{
  "success": true,
  "data": {
    "username": "trolluser",
    "moderated": true,
    "action": "ban",
    "reason": "Harassment",
    "moderator": "admin",
    "timestamp": "2025-12-14T10:30:00Z",
    "ips": ["192.168.1.x", "10.0.0.x"],
    "ip_correlation_source": null
  }
}
```

For correlated entries:

```json
{
  "success": true,
  "data": {
    "username": "trolluser_alt",
    "moderated": true,
    "action": "ban",
    "reason": "IP correlation with trolluser: Harassment",
    "moderator": "system:ip_correlation",
    "timestamp": "2025-12-14T11:00:00Z",
    "ips": ["192.168.1.x"],
    "ip_correlation_source": "trolluser"
  }
}
```

---

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `kryten_moderator/ip_manager.py` | **NEW** | IPManager class for IP correlation |
| `kryten_moderator/moderation_list.py` | **MODIFY** | Add update(), add_ip_to_entry() methods |
| `kryten_moderator/service.py` | **MODIFY** | IP extraction, correlation logic |
| `kryten_moderator/metrics_server.py` | **MODIFY** | Add IP correlation metrics |
| `pyproject.toml` | **MODIFY** | Bump version to 0.4.0 |

---

## Testing Checklist

### Unit Tests

- [ ] IPManager.add_ip() stores IP mapping in KV
- [ ] IPManager.remove_ip() removes IP mapping
- [ ] IPManager.check_ip_correlation() finds matching moderated users
- [ ] IPManager.check_ip_correlation() excludes self-matches
- [ ] IP masking works correctly for privacy
- [ ] ModerationList.add_ip_to_entry() updates entry IPs

### Integration Tests

- [ ] IP map bucket is created on startup
- [ ] IP is extracted from user join events
- [ ] IP is stored with moderation entry when user is actioned
- [ ] New account from same IP is auto-moderated
- [ ] Correlated entry references source username
- [ ] `entry.get` returns IPs and correlation source
- [ ] Metrics include IP correlation counter

### Manual Testing

- [ ] Add ban entry for user with known IP
- [ ] Join with different username from same IP
- [ ] Verify new account is automatically banned
- [ ] Check logs show IP correlation detection
- [ ] Verify correlated entry shows in `entry.list`

---

## Privacy Considerations

- IPs are masked in all log output (e.g., "192.168.x.x")
- Full IPs stored only in KV (not visible in normal responses)
- CLI `check` command shows masked IPs only
- Consider GDPR: provide option to purge IP data on unban

---

## Edge Cases

1. **No IP available**: Skip IP correlation, only use username matching
2. **VPN users**: Same user may have multiple IPs - store all of them
3. **Shared IP (NAT)**: May cause false positives - moderator can override
4. **Dynamic IP**: Correlation still works if original IP is in window
5. **Masked IP only**: Use masked IP for partial matching (less accurate)

---

## Dependencies

- Phase 1 complete (ModerationList functional)
- `kryten-py >= 0.9.4`
- NATS JetStream enabled

---

## Version

After completing Phase 2: `kryten-moderator v0.4.0`
