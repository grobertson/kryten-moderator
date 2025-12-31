"""Unit tests for the moderation_list module."""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from kryten_moderator.moderation_list import (
    ModerationEntry,
    ModerationList,
    ModerationListManager,
    make_bucket_name,
)


class TestModerationEntry:
    """Tests for ModerationEntry dataclass."""

    def test_create_entry(self):
        """Test creating a basic moderation entry."""
        entry = ModerationEntry(
            username="baduser",
            action="smute",
            reason="trolling",
            moderator="admin",
            timestamp="2025-12-14T10:00:00+00:00",
            ips=["192.168.1.1"],
        )
        
        assert entry.username == "baduser"
        assert entry.action == "smute"
        assert entry.reason == "trolling"
        assert entry.moderator == "admin"
        assert entry.ips == ["192.168.1.1"]
        assert entry.ip_correlation_source is None
        assert entry.pattern_match is None

    def test_to_json(self):
        """Test serializing entry to JSON."""
        entry = ModerationEntry(
            username="TestUser",
            action="ban",
            reason="spam",
            moderator="mod1",
            timestamp="2025-12-14T10:00:00+00:00",
            ips=[],
        )
        
        json_str = entry.to_json()
        data = json.loads(json_str)
        
        assert data["username"] == "TestUser"
        assert data["action"] == "ban"
        assert data["reason"] == "spam"

    def test_from_json(self):
        """Test deserializing entry from JSON."""
        json_str = json.dumps({
            "username": "restored_user",
            "action": "mute",
            "reason": "caps spam",
            "moderator": "system",
            "timestamp": "2025-12-14T10:00:00+00:00",
            "ips": ["10.0.0.1"],
            "ip_correlation_source": None,
            "pattern_match": None,
        })
        
        entry = ModerationEntry.from_json(json_str)
        
        assert entry.username == "restored_user"
        assert entry.action == "mute"
        assert entry.ips == ["10.0.0.1"]

    def test_to_dict(self):
        """Test converting entry to dictionary."""
        entry = ModerationEntry(
            username="dictuser",
            action="smute",
            reason=None,
            moderator="cli",
            timestamp="2025-12-14T10:00:00+00:00",
            ips=[],
        )
        
        d = entry.to_dict()
        
        assert isinstance(d, dict)
        assert d["username"] == "dictuser"
        assert d["action"] == "smute"
        assert d["reason"] is None


class TestMakeBucketName:
    """Tests for bucket name generation."""

    def test_basic_bucket_name(self):
        """Test basic bucket name creation."""
        name = make_bucket_name("cytu.be", "lounge")
        assert name == "kryten_moderator_entries_cytu_be_lounge"

    def test_bucket_name_with_dashes(self):
        """Test bucket name with channel containing dashes."""
        name = make_bucket_name("cytu.be", "my-channel")
        assert name == "kryten_moderator_entries_cytu_be_my_channel"

    def test_bucket_name_uppercase(self):
        """Test bucket name normalizes to lowercase."""
        name = make_bucket_name("cytu.be", "LOUNGE")
        assert name == "kryten_moderator_entries_cytu_be_lounge"


class TestModerationList:
    """Tests for ModerationList class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock KrytenClient."""
        client = MagicMock()
        client.kv_keys = AsyncMock(return_value=[])
        client.kv_get = AsyncMock(return_value=None)
        client.kv_put = AsyncMock()
        client.kv_delete = AsyncMock()
        client.get_or_create_kv_bucket = AsyncMock(return_value=AsyncMock())
        return client

    @pytest.mark.asyncio
    async def test_initialize_empty(self, mock_client):
        """Test initializing an empty moderation list."""
        mod_list = ModerationList(mock_client, "cytu.be", "lounge")
        await mod_list.initialize()
        
        assert mod_list._initialized is True
        assert mod_list.size == 0
        mock_client.kv_keys.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_entry(self, mock_client):
        """Test adding a moderation entry."""
        mod_list = ModerationList(mock_client, "cytu.be", "lounge")
        await mod_list.initialize()
        
        entry = await mod_list.add(
            username="BadUser",
            action="smute",
            moderator="admin",
            reason="trolling",
        )
        
        assert entry.username == "BadUser"
        assert entry.action == "smute"
        assert mod_list.size == 1
        mock_client.kv_put.assert_called()

    @pytest.mark.asyncio
    async def test_check_username_found(self, mock_client):
        """Test checking a username that exists."""
        mod_list = ModerationList(mock_client, "cytu.be", "lounge")
        await mod_list.initialize()
        await mod_list.add("troll", "smute", "admin")
        
        # Check with same case
        entry = mod_list.check_username("troll")
        assert entry is not None
        assert entry.action == "smute"
        
        # Check case-insensitive
        entry = mod_list.check_username("TROLL")
        assert entry is not None

    @pytest.mark.asyncio
    async def test_check_username_not_found(self, mock_client):
        """Test checking a username that doesn't exist."""
        mod_list = ModerationList(mock_client, "cytu.be", "lounge")
        await mod_list.initialize()
        
        entry = mod_list.check_username("innocent_user")
        assert entry is None

    @pytest.mark.asyncio
    async def test_remove_entry(self, mock_client):
        """Test removing a moderation entry."""
        mod_list = ModerationList(mock_client, "cytu.be", "lounge")
        await mod_list.initialize()
        await mod_list.add("removeme", "ban", "admin")
        
        assert mod_list.size == 1
        
        removed = await mod_list.remove("removeme")
        assert removed is True
        assert mod_list.size == 0
        mock_client.kv_delete.assert_called()

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self, mock_client):
        """Test removing a user not in the list."""
        mod_list = ModerationList(mock_client, "cytu.be", "lounge")
        await mod_list.initialize()
        
        removed = await mod_list.remove("nobody")
        assert removed is False

    @pytest.mark.asyncio
    async def test_list_all(self, mock_client):
        """Test listing all entries."""
        mod_list = ModerationList(mock_client, "cytu.be", "lounge")
        await mod_list.initialize()
        
        await mod_list.add("user1", "ban", "admin")
        await mod_list.add("user2", "smute", "admin")
        await mod_list.add("user3", "mute", "admin")
        
        entries = await mod_list.list_all()
        assert len(entries) == 3

    @pytest.mark.asyncio
    async def test_list_all_filtered(self, mock_client):
        """Test listing entries with filter."""
        mod_list = ModerationList(mock_client, "cytu.be", "lounge")
        await mod_list.initialize()
        
        await mod_list.add("user1", "ban", "admin")
        await mod_list.add("user2", "smute", "admin")
        await mod_list.add("user3", "smute", "admin")
        
        smute_entries = await mod_list.list_all(filter_action="smute")
        assert len(smute_entries) == 2
        assert all(e.action == "smute" for e in smute_entries)


class TestModerationListManager:
    """Tests for ModerationListManager class."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock KrytenClient."""
        client = MagicMock()
        client.kv_keys = AsyncMock(return_value=[])
        client.kv_get = AsyncMock(return_value=None)
        client.kv_put = AsyncMock()
        client.kv_delete = AsyncMock()
        client.get_or_create_kv_bucket = AsyncMock(return_value=AsyncMock())
        return client

    @pytest.mark.asyncio
    async def test_get_list(self, mock_client):
        """Test getting a moderation list for a channel."""
        manager = ModerationListManager(mock_client)
        
        mod_list = await manager.get_list("cytu.be", "lounge")
        
        assert mod_list is not None
        assert mod_list.domain == "cytu.be"
        assert mod_list.channel == "lounge"

    @pytest.mark.asyncio
    async def test_get_list_caches(self, mock_client):
        """Test that get_list caches the list."""
        manager = ModerationListManager(mock_client)
        
        list1 = await manager.get_list("cytu.be", "lounge")
        list2 = await manager.get_list("cytu.be", "lounge")
        
        assert list1 is list2

    @pytest.mark.asyncio
    async def test_initialize_all(self, mock_client):
        """Test initializing all channel lists."""
        manager = ModerationListManager(mock_client)
        
        channels = [
            {"domain": "cytu.be", "channel": "lounge"},
            {"domain": "cytu.be", "channel": "movies"},
        ]
        
        await manager.initialize_all(channels)
        
        assert manager.list_count == 2

    def test_check_username(self, mock_client):
        """Test synchronous username check."""
        manager = ModerationListManager(mock_client)
        
        # Before initialization, should return None
        result = manager.check_username("cytu.be", "lounge", "someone")
        assert result is None
