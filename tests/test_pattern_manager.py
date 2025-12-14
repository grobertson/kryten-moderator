"""Unit tests for the pattern_manager module."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from kryten_moderator.pattern_manager import (
    PatternEntry,
    CompiledPattern,
    PatternManager,
    PatternManagerRegistry,
    make_bucket_name,
)


class TestPatternEntry:
    """Tests for PatternEntry dataclass."""

    def test_create_substring_pattern(self):
        """Test creating a substring pattern entry."""
        entry = PatternEntry(
            pattern="1488",
            is_regex=False,
            action="ban",
            added_by="admin",
            timestamp="2025-12-14T10:00:00+00:00",
            description="Nazi hate number",
        )
        
        assert entry.pattern == "1488"
        assert entry.is_regex is False
        assert entry.action == "ban"

    def test_create_regex_pattern(self):
        """Test creating a regex pattern entry."""
        entry = PatternEntry(
            pattern=r"^.*hitler.*$",
            is_regex=True,
            action="ban",
            added_by="admin",
            timestamp="2025-12-14T10:00:00+00:00",
        )
        
        assert entry.is_regex is True


class TestCompiledPattern:
    """Tests for CompiledPattern class."""

    def test_substring_match(self):
        """Test substring pattern matching."""
        entry = PatternEntry(
            pattern="spam",
            is_regex=False,
            action="smute",
            added_by="admin",
            timestamp="2025-12-14T10:00:00+00:00",
        )
        compiled = CompiledPattern(entry)
        
        assert compiled.matches("spammer123") is True
        assert compiled.matches("SPAMBOT") is True  # Case insensitive
        assert compiled.matches("normaluser") is False

    def test_regex_match(self):
        """Test regex pattern matching."""
        entry = PatternEntry(
            pattern=r"^troll\d+$",
            is_regex=True,
            action="ban",
            added_by="admin",
            timestamp="2025-12-14T10:00:00+00:00",
        )
        compiled = CompiledPattern(entry)
        
        assert compiled.matches("troll123") is True
        assert compiled.matches("troll1") is True
        assert compiled.matches("TROLL999") is True  # Case insensitive
        assert compiled.matches("troll") is False
        assert compiled.matches("sometroll123here") is False  # Must match full pattern

    def test_invalid_regex_raises(self):
        """Test that invalid regex raises ValueError."""
        entry = PatternEntry(
            pattern=r"[invalid(regex",
            is_regex=True,
            action="ban",
            added_by="admin",
            timestamp="2025-12-14T10:00:00+00:00",
        )
        
        with pytest.raises(ValueError, match="Invalid regex"):
            CompiledPattern(entry)

    def test_special_chars_in_substring(self):
        """Test that special regex chars are escaped in substring mode."""
        entry = PatternEntry(
            pattern="user.name",  # . is regex special char
            is_regex=False,
            action="smute",
            added_by="admin",
            timestamp="2025-12-14T10:00:00+00:00",
        )
        compiled = CompiledPattern(entry)
        
        assert compiled.matches("user.name") is True
        assert compiled.matches("username") is False  # . should not match any char


class TestMakeBucketName:
    """Tests for bucket name generation."""

    def test_basic_bucket_name(self):
        """Test basic bucket name creation."""
        name = make_bucket_name("cytu.be", "lounge")
        assert name == "kryten_moderator_patterns_cytu_be_lounge"


class TestPatternManager:
    """Tests for PatternManager class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock KrytenClient."""
        client = MagicMock()
        client.kv_keys = AsyncMock(return_value=[])
        client.kv_get = AsyncMock(return_value=None)
        client.kv_put = AsyncMock()
        client.kv_delete = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_initialize_empty(self, mock_client):
        """Test initializing an empty pattern manager."""
        manager = PatternManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        
        assert manager._initialized is True
        assert manager.count == 0

    @pytest.mark.asyncio
    async def test_add_pattern(self, mock_client):
        """Test adding a pattern."""
        manager = PatternManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        
        entry = await manager.add(
            pattern="badword",
            is_regex=False,
            action="smute",
            added_by="admin",
        )
        
        assert entry.pattern == "badword"
        assert manager.count == 1
        mock_client.kv_put.assert_called()

    @pytest.mark.asyncio
    async def test_check_username_match(self, mock_client):
        """Test checking a username that matches a pattern."""
        manager = PatternManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        await manager.add("troll", False, "ban", "admin")
        
        result = manager.check_username("troll_master")
        
        assert result is not None
        pattern_entry, matched = result
        assert matched == "troll"
        assert pattern_entry.action == "ban"

    @pytest.mark.asyncio
    async def test_check_username_no_match(self, mock_client):
        """Test checking a username that doesn't match."""
        manager = PatternManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        await manager.add("badpattern", False, "ban", "admin")
        
        result = manager.check_username("normaluser")
        assert result is None

    @pytest.mark.asyncio
    async def test_remove_pattern(self, mock_client):
        """Test removing a pattern."""
        manager = PatternManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        await manager.add("remove_me", False, "ban", "admin")
        
        assert manager.count == 1
        
        removed = await manager.remove("remove_me")
        assert removed is True
        assert manager.count == 0

    @pytest.mark.asyncio
    async def test_list_all(self, mock_client):
        """Test listing all patterns."""
        manager = PatternManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        
        await manager.add("pattern1", False, "ban", "admin")
        await manager.add("pattern2", True, "smute", "admin")
        
        entries = await manager.list_all()
        assert len(entries) == 2

    @pytest.mark.asyncio
    async def test_default_patterns(self, mock_client):
        """Test adding default patterns on initialization."""
        manager = PatternManager(mock_client, "cytu.be", "lounge")
        
        defaults = [
            "1488",
            {"pattern": "卐", "action": "ban", "description": "Swastika"},
        ]
        
        await manager.initialize(default_patterns=defaults)
        
        assert manager.count == 2


class TestPatternManagerRegistry:
    """Tests for PatternManagerRegistry class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock KrytenClient."""
        client = MagicMock()
        client.kv_keys = AsyncMock(return_value=[])
        client.kv_get = AsyncMock(return_value=None)
        client.kv_put = AsyncMock()
        client.kv_delete = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_get_manager(self, mock_client):
        """Test getting a pattern manager for a channel."""
        registry = PatternManagerRegistry(mock_client)
        
        manager = await registry.get_manager("cytu.be", "lounge")
        
        assert manager is not None
        assert manager.domain == "cytu.be"
        assert manager.channel == "lounge"

    @pytest.mark.asyncio
    async def test_initialize_all_with_defaults(self, mock_client):
        """Test initializing all channels with default patterns."""
        registry = PatternManagerRegistry(mock_client)
        
        channels = [
            {"domain": "cytu.be", "channel": "lounge"},
            {"domain": "cytu.be", "channel": "movies"},
        ]
        defaults = ["badword"]
        
        await registry.initialize_all(channels, defaults)
        
        assert registry.manager_count == 2
        assert registry.total_patterns == 2  # 1 default per channel
