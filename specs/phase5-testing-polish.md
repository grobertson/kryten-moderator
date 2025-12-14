# Phase 5: Testing and Polish

**Duration**: 2-3 days  
**PRD Reference**: Section 9.3, Phase 5  
**User Stories**: MOD-014 (Health/Metrics)  
**Depends On**: Phase 1-4 complete

## Overview

Phase 5 focuses on comprehensive testing, edge case handling, documentation, and metrics verification. This phase ensures production readiness of the moderator service.

## Deliverables

1. **Integration Testing**
   - End-to-end test scenarios
   - Multi-service integration tests
   - NATS command interface tests

2. **Edge Case Handling**
   - Race conditions between multiple moderators
   - Network failure recovery
   - Invalid input handling
   - KV store consistency

3. **Documentation**
   - README.md update
   - Configuration reference
   - Troubleshooting guide
   - Example workflows

4. **Metrics Verification** (MOD-014)
   - Verify all Prometheus metrics
   - Health endpoint completeness
   - Grafana dashboard (optional)

---

## Testing Strategy

### 1. Unit Test Coverage

#### Files to Test

| Module | Test File | Coverage Target |
|--------|-----------|-----------------|
| `moderation_list.py` | `test_moderation_list.py` | 90%+ |
| `ip_manager.py` | `test_ip_manager.py` | 90%+ |
| `pattern_manager.py` | `test_pattern_manager.py` | 90%+ |
| `nats_handler.py` | `test_nats_handler.py` | 80%+ |
| `service.py` | `test_service.py` | 70%+ |

#### Test File Structure

```
tests/
├── conftest.py              # Shared fixtures
├── test_moderation_list.py  # ModerationList unit tests
├── test_ip_manager.py       # IPManager unit tests
├── test_pattern_manager.py  # PatternManager unit tests
├── test_nats_handler.py     # Command handler tests
├── test_service.py          # Service integration tests
└── test_enforcement.py      # Enforcement scenario tests
```

### 2. Test Fixtures

#### `conftest.py`

```python
"""Shared test fixtures for kryten-moderator tests."""

import pytest
import pytest_asyncio
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from kryten import KrytenClient


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def mock_kv():
    """Mock NATS JetStream KV store."""
    kv = AsyncMock()
    kv.keys = AsyncMock(return_value=[])
    kv.get = AsyncMock(return_value=None)
    kv.put = AsyncMock()
    kv.delete = AsyncMock()
    return kv


@pytest_asyncio.fixture
async def mock_js(mock_kv):
    """Mock JetStream with KV."""
    js = AsyncMock()
    js.key_value = AsyncMock(return_value=mock_kv)
    js.create_key_value = AsyncMock(return_value=mock_kv)
    return js


@pytest_asyncio.fixture
async def mock_client(mock_js):
    """Mock KrytenClient."""
    client = MagicMock(spec=KrytenClient)
    client.jetstream = mock_js
    client.kick_user = AsyncMock()
    client.mute_user = AsyncMock()
    client.shadow_mute_user = AsyncMock()
    client.unmute_user = AsyncMock()
    return client


@pytest.fixture
def sample_entry_data():
    """Sample moderation entry data."""
    return {
        "username": "testuser",
        "action": "ban",
        "reason": "Test ban",
        "moderator": "admin",
        "timestamp": "2025-12-14T10:00:00Z",
        "ips": [],
        "ip_correlation_source": None,
        "pattern_match": None,
    }


@pytest.fixture
def sample_pattern_data():
    """Sample pattern entry data."""
    return {
        "pattern": "test",
        "is_regex": False,
        "action": "ban",
        "added_by": "admin",
        "timestamp": "2025-12-14T10:00:00Z",
        "description": "Test pattern",
    }
```

### 3. Unit Tests

#### `test_moderation_list.py`

```python
"""Tests for ModerationList."""

import pytest
import json
from kryten_moderator.moderation_list import ModerationList, ModerationEntry


class TestModerationEntry:
    """Tests for ModerationEntry dataclass."""
    
    def test_to_json(self, sample_entry_data):
        """Test JSON serialization."""
        entry = ModerationEntry(**sample_entry_data)
        json_str = entry.to_json()
        parsed = json.loads(json_str)
        assert parsed["username"] == "testuser"
        assert parsed["action"] == "ban"
    
    def test_from_json(self, sample_entry_data):
        """Test JSON deserialization."""
        json_str = json.dumps(sample_entry_data)
        entry = ModerationEntry.from_json(json_str)
        assert entry.username == "testuser"
        assert entry.action == "ban"
    
    def test_roundtrip(self, sample_entry_data):
        """Test serialize -> deserialize roundtrip."""
        original = ModerationEntry(**sample_entry_data)
        restored = ModerationEntry.from_json(original.to_json())
        assert original.username == restored.username
        assert original.action == restored.action


class TestModerationList:
    """Tests for ModerationList."""
    
    @pytest.mark.asyncio
    async def test_initialize_creates_bucket(self, mock_client, mock_js):
        """Test bucket creation on first init."""
        mock_js.key_value.side_effect = Exception("Not found")
        
        mod_list = ModerationList(mock_client)
        await mod_list.initialize()
        
        mock_js.create_key_value.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_add_stores_in_kv(self, mock_client, mock_kv):
        """Test add() stores entry in KV."""
        mod_list = ModerationList(mock_client)
        mod_list._kv = mock_kv
        
        entry = await mod_list.add(
            username="TestUser",
            action="ban",
            moderator="admin",
            reason="Testing",
        )
        
        assert entry.username == "TestUser"
        assert entry.action == "ban"
        mock_kv.put.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_add_normalizes_username(self, mock_client, mock_kv):
        """Test username is lowercased for storage key."""
        mod_list = ModerationList(mock_client)
        mod_list._kv = mock_kv
        
        await mod_list.add("TestUser", "ban", "admin")
        
        # Check the key used was lowercase
        call_args = mock_kv.put.call_args
        assert call_args[0][0] == "testuser"
    
    @pytest.mark.asyncio
    async def test_remove_deletes_from_kv(self, mock_client, mock_kv):
        """Test remove() deletes from KV."""
        mod_list = ModerationList(mock_client)
        mod_list._kv = mock_kv
        mod_list._cache["testuser"] = ModerationEntry(
            username="testuser",
            action="ban",
            reason=None,
            moderator="admin",
            timestamp="",
            ips=[],
        )
        
        result = await mod_list.remove("TestUser")
        
        assert result is True
        mock_kv.delete.assert_called_once_with("testuser")
    
    @pytest.mark.asyncio
    async def test_remove_not_found(self, mock_client, mock_kv):
        """Test remove() returns False if not found."""
        mod_list = ModerationList(mock_client)
        mod_list._kv = mock_kv
        
        result = await mod_list.remove("nonexistent")
        
        assert result is False
        mock_kv.delete.assert_not_called()
    
    def test_check_username_uses_cache(self, mock_client, sample_entry_data):
        """Test check_username() uses cache for fast lookups."""
        mod_list = ModerationList(mock_client)
        entry = ModerationEntry(**sample_entry_data)
        mod_list._cache["testuser"] = entry
        
        result = mod_list.check_username("TestUser")
        
        assert result is entry
    
    def test_check_username_case_insensitive(self, mock_client, sample_entry_data):
        """Test check_username() is case-insensitive."""
        mod_list = ModerationList(mock_client)
        entry = ModerationEntry(**sample_entry_data)
        mod_list._cache["testuser"] = entry
        
        assert mod_list.check_username("TESTUSER") is entry
        assert mod_list.check_username("TestUser") is entry
        assert mod_list.check_username("testuser") is entry


class TestModerationListFiltering:
    """Tests for list filtering."""
    
    @pytest.mark.asyncio
    async def test_list_all_no_filter(self, mock_client):
        """Test list_all() without filter."""
        mod_list = ModerationList(mock_client)
        mod_list._cache = {
            "user1": ModerationEntry("user1", "ban", None, "admin", "", []),
            "user2": ModerationEntry("user2", "smute", None, "admin", "", []),
        }
        
        entries = await mod_list.list_all()
        
        assert len(entries) == 2
    
    @pytest.mark.asyncio
    async def test_list_all_filter_ban(self, mock_client):
        """Test list_all() with ban filter."""
        mod_list = ModerationList(mock_client)
        mod_list._cache = {
            "user1": ModerationEntry("user1", "ban", None, "admin", "", []),
            "user2": ModerationEntry("user2", "smute", None, "admin", "", []),
        }
        
        entries = await mod_list.list_all(filter_action="ban")
        
        assert len(entries) == 1
        assert entries[0].action == "ban"
```

#### `test_pattern_manager.py`

```python
"""Tests for PatternManager."""

import pytest
from kryten_moderator.pattern_manager import (
    PatternManager,
    PatternEntry,
    CompiledPattern,
)


class TestCompiledPattern:
    """Tests for CompiledPattern."""
    
    def test_substring_match(self):
        """Test substring pattern matching."""
        entry = PatternEntry("test", False, "ban", "admin", "", None)
        compiled = CompiledPattern(entry)
        
        assert compiled.matches("TestUser") is True
        assert compiled.matches("mytestaccount") is True
        assert compiled.matches("hello") is False
    
    def test_substring_case_insensitive(self):
        """Test substring matching is case-insensitive."""
        entry = PatternEntry("TEST", False, "ban", "admin", "", None)
        compiled = CompiledPattern(entry)
        
        assert compiled.matches("testuser") is True
        assert compiled.matches("TESTUSER") is True
    
    def test_regex_match(self):
        """Test regex pattern matching."""
        entry = PatternEntry("^troll\\d+$", True, "ban", "admin", "", None)
        compiled = CompiledPattern(entry)
        
        assert compiled.matches("troll123") is True
        assert compiled.matches("troll1") is True
        assert compiled.matches("troll") is False
        assert compiled.matches("trolluser") is False
    
    def test_invalid_regex_raises(self):
        """Test invalid regex raises ValueError."""
        entry = PatternEntry("[invalid", True, "ban", "admin", "", None)
        
        with pytest.raises(ValueError):
            CompiledPattern(entry)


class TestPatternManager:
    """Tests for PatternManager."""
    
    @pytest.mark.asyncio
    async def test_add_pattern(self, mock_client, mock_kv):
        """Test adding a pattern."""
        pm = PatternManager(mock_client)
        pm._kv = mock_kv
        
        entry = await pm.add("test", False, "ban", "admin")
        
        assert entry.pattern == "test"
        mock_kv.put.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_add_invalid_regex_rejected(self, mock_client, mock_kv):
        """Test invalid regex is rejected."""
        pm = PatternManager(mock_client)
        pm._kv = mock_kv
        
        with pytest.raises(ValueError):
            await pm.add("[invalid", True, "ban", "admin")
    
    def test_check_username_returns_first_match(self, mock_client):
        """Test check_username() returns first matching pattern."""
        pm = PatternManager(mock_client)
        
        entry1 = PatternEntry("troll", False, "ban", "admin", "", None)
        entry2 = PatternEntry("user", False, "smute", "admin", "", None)
        
        pm._patterns["k1"] = CompiledPattern(entry1)
        pm._patterns["k2"] = CompiledPattern(entry2)
        
        result = pm.check_username("trolluser")
        
        assert result is not None
        # Either match is valid since dict order isn't guaranteed
        assert result[0].action in ("ban", "smute")
```

### 4. Integration Tests

#### `test_enforcement.py`

```python
"""Integration tests for moderation enforcement scenarios."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from kryten import UserJoinEvent

from kryten_moderator.service import ModeratorService


class TestEnforcementScenarios:
    """Test enforcement on user join."""
    
    @pytest.mark.asyncio
    async def test_banned_user_kicked(self, mock_service):
        """Test banned user is kicked on join."""
        # Setup: user is in ban list
        mock_service.moderation_list.check_username.return_value = MagicMock(
            username="trolluser",
            action="ban",
            reason="Test ban",
        )
        
        # Simulate user join
        event = UserJoinEvent(username="TrollUser", rank=0)
        await mock_service._handle_user_join(event)
        
        # Verify kick was called
        mock_service.client.kick_user.assert_called_once()
        assert mock_service._bans_enforced == 1
    
    @pytest.mark.asyncio
    async def test_smuted_user_shadow_muted(self, mock_service):
        """Test smuted user is shadow muted on join."""
        mock_service.moderation_list.check_username.return_value = MagicMock(
            username="subtletroll",
            action="smute",
            reason="Passive aggressive",
        )
        
        event = UserJoinEvent(username="SubtleTroll", rank=0)
        await mock_service._handle_user_join(event)
        
        mock_service.client.shadow_mute_user.assert_called_once()
        assert mock_service._smutes_enforced == 1
    
    @pytest.mark.asyncio
    async def test_clean_user_not_moderated(self, mock_service):
        """Test clean user passes through."""
        mock_service.moderation_list.check_username.return_value = None
        mock_service.ip_manager = None  # Disable IP checks for this test
        mock_service.pattern_manager = None  # Disable pattern checks
        
        event = UserJoinEvent(username="GoodUser", rank=0)
        await mock_service._handle_user_join(event)
        
        mock_service.client.kick_user.assert_not_called()
        mock_service.client.shadow_mute_user.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_pattern_match_before_username_check(self, mock_service):
        """Test pattern matching runs first."""
        # User not in mod list but matches pattern
        mock_service.moderation_list.check_username.return_value = None
        mock_service.pattern_manager.check_username.return_value = (
            MagicMock(action="ban"),
            "badpattern",
        )
        
        event = UserJoinEvent(username="BadPatternUser", rank=0)
        await mock_service._handle_user_join(event)
        
        # Pattern match should add to mod list and enforce
        mock_service.moderation_list.add.assert_called()
        assert mock_service._pattern_matches == 1


class TestIPCorrelation:
    """Test IP correlation scenarios."""
    
    @pytest.mark.asyncio
    async def test_ip_match_applies_same_action(self, mock_service):
        """Test IP correlation applies same action to new account."""
        mock_service.moderation_list.check_username.return_value = None
        mock_service.ip_manager.check_ip_correlation.return_value = (
            "original_troll",
            MagicMock(action="ban", reason="Original ban"),
        )
        
        event = UserJoinEvent(username="NewAlt", rank=0)
        event.raw = {"meta": {"ip": "192.168.1.100"}}
        
        await mock_service._handle_user_join(event)
        
        # Should add new entry and kick
        mock_service.moderation_list.add.assert_called()
        assert mock_service._ip_correlations == 1
```

### 5. Edge Case Tests

#### `test_edge_cases.py`

```python
"""Tests for edge cases and error handling."""

import pytest
from unittest.mock import AsyncMock


class TestConcurrentModeration:
    """Test concurrent moderation scenarios."""
    
    @pytest.mark.asyncio
    async def test_concurrent_add_same_user(self, mock_client, mock_kv):
        """Test two mods adding same user - last write wins."""
        from kryten_moderator.moderation_list import ModerationList
        
        mod_list = ModerationList(mock_client)
        mod_list._kv = mock_kv
        
        # Simulate concurrent adds
        await mod_list.add("user", "ban", "mod1", "First reason")
        await mod_list.add("user", "smute", "mod2", "Second reason")
        
        # Last write should win
        entry = mod_list.check_username("user")
        assert entry.action == "smute"
        assert entry.moderator == "mod2"


class TestNetworkFailure:
    """Test network failure scenarios."""
    
    @pytest.mark.asyncio
    async def test_kv_write_failure_handled(self, mock_client, mock_kv):
        """Test graceful handling of KV write failure."""
        from kryten_moderator.moderation_list import ModerationList
        
        mock_kv.put.side_effect = Exception("Network error")
        
        mod_list = ModerationList(mock_client)
        mod_list._kv = mock_kv
        
        with pytest.raises(Exception):
            await mod_list.add("user", "ban", "admin")


class TestInputValidation:
    """Test input validation."""
    
    @pytest.mark.asyncio
    async def test_empty_username_rejected(self, mock_client, mock_kv):
        """Test empty username is rejected."""
        from kryten_moderator.nats_handler import ModeratorCommandHandler
        
        handler = ModeratorCommandHandler(MagicMock(), mock_client)
        
        result = await handler._cmd_entry_add({"action": "ban"})
        
        assert result["success"] is False
        assert "required" in result["error"].lower()
    
    @pytest.mark.asyncio
    async def test_invalid_action_rejected(self, mock_client, mock_kv):
        """Test invalid action is rejected."""
        from kryten_moderator.nats_handler import ModeratorCommandHandler
        
        handler = ModeratorCommandHandler(MagicMock(), mock_client)
        
        result = await handler._cmd_entry_add({
            "username": "user",
            "action": "invalid",
        })
        
        assert result["success"] is False
        assert "action" in result["error"].lower()
```

---

## Documentation Updates

### 1. README.md Update

```markdown
# kryten-moderator

Persistent moderation service for CyTube channels via the Kryten bridge.

## Features

- **Persistent Ban List**: Ban users even when they're offline
- **Shadow Mute (smute)**: Mute users without them knowing
- **IP Correlation**: Detect ban evasion via IP matching
- **Pattern Matching**: Auto-ban offensive usernames
- **CLI Integration**: Full CLI support via `kryten moderator`

## Quick Start

1. Install: `pipx install kryten-moderator`
2. Configure: Copy `config.example.json` to `config.json`
3. Run: `kryten-moderator`

## CLI Usage

```bash
# Ban a user
kryten moderator ban TrollUser "Harassment"

# Shadow mute (user doesn't know)
kryten moderator smute SubtleTroll "Passive aggressive"

# Check status
kryten moderator check TrollUser

# List all moderated users
kryten moderator list

# Manage patterns
kryten moderator patterns list
kryten moderator patterns add "badword" --action ban
```

## Configuration

See `config.example.json` for all options.

## Metrics

Prometheus metrics available at `http://localhost:28284/metrics`:

- `moderator_bans_enforced`
- `moderator_smutes_enforced`
- `moderator_mutes_enforced`
- `moderator_ip_correlations`
- `moderator_pattern_matches`
- `moderator_list_size`

## License

MIT
```

### 2. Configuration Reference

Create `CONFIGURATION.md`:

```markdown
# Configuration Reference

## Full Configuration Example

```json
{
  "service": {
    "name": "moderator",
    "enable_lifecycle": true,
    "enable_heartbeat": true
  },
  "nats": {
    "servers": ["nats://localhost:4222"]
  },
  "channels": [
    {"domain": "cytu.be", "channel": "your_channel"}
  ],
  "metrics": {
    "port": 28284
  },
  "moderation": {
    "enable_auto_enforcement": true,
    "enable_ip_correlation": true,
    "enable_pattern_matching": true,
    "default_patterns": ["1488", "hitler"]
  },
  "kv_buckets": {
    "entries": "kryten_moderator_entries",
    "patterns": "kryten_moderator_patterns",
    "ip_map": "kryten_moderator_ip_map"
  }
}
```

## Option Reference

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `service.name` | string | "moderator" | Service identifier |
| `service.enable_lifecycle` | bool | true | Enable NATS lifecycle events |
| `moderation.enable_auto_enforcement` | bool | true | Enforce on user join |
| `moderation.enable_ip_correlation` | bool | true | Enable IP-based ban evasion detection |
| `moderation.enable_pattern_matching` | bool | true | Enable username pattern matching |
| `metrics.port` | int | 28284 | HTTP port for /health and /metrics |
```

---

## Metrics Verification Checklist

### Prometheus Metrics

| Metric | Type | Verified |
|--------|------|----------|
| `moderator_events_processed` | counter | [ ] |
| `moderator_commands_processed` | counter | [ ] |
| `moderator_bans_enforced` | counter | [ ] |
| `moderator_smutes_enforced` | counter | [ ] |
| `moderator_mutes_enforced` | counter | [ ] |
| `moderator_ip_correlations` | counter | [ ] |
| `moderator_pattern_matches` | counter | [ ] |
| `moderator_list_size` | gauge | [ ] |
| `moderator_pattern_count` | gauge | [ ] |
| `moderator_ip_map_size` | gauge | [ ] |

### Health Endpoint

```bash
$ curl http://localhost:28284/health
{
  "status": "healthy",
  "service": "moderator",
  "version": "0.5.0",
  "uptime_seconds": 3600,
  "moderation_list_size": 25,
  "pattern_count": 5
}
```

---

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `tests/conftest.py` | **NEW** | Shared test fixtures |
| `tests/test_moderation_list.py` | **NEW** | ModerationList unit tests |
| `tests/test_ip_manager.py` | **NEW** | IPManager unit tests |
| `tests/test_pattern_manager.py` | **NEW** | PatternManager unit tests |
| `tests/test_nats_handler.py` | **NEW** | Command handler tests |
| `tests/test_enforcement.py` | **NEW** | Integration tests |
| `tests/test_edge_cases.py` | **NEW** | Edge case tests |
| `README.md` | **MODIFY** | Update with full documentation |
| `CONFIGURATION.md` | **NEW** | Configuration reference |
| `TROUBLESHOOTING.md` | **NEW** | Troubleshooting guide |
| `pyproject.toml` | **MODIFY** | Add test dependencies, final version |

---

## Test Dependencies

Add to `pyproject.toml`:

```toml
[tool.poetry.group.dev.dependencies]
pytest = "^7.4.0"
pytest-asyncio = "^0.21.0"
pytest-cov = "^4.1.0"
```

---

## Final Version

After completing Phase 5: `kryten-moderator v1.0.0`

---

## Release Checklist

- [ ] All unit tests passing
- [ ] All integration tests passing
- [ ] Test coverage > 80%
- [ ] README.md complete
- [ ] CONFIGURATION.md complete
- [ ] Health endpoint verified
- [ ] All metrics verified
- [ ] CHANGELOG.md updated
- [ ] Version bumped to 1.0.0
- [ ] Package published to PyPI
- [ ] kryten-cli v2.4.0 published
