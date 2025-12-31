"""Unit tests for the ip_manager module."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from kryten_moderator.ip_manager import (
    IPManager,
    IPManagerRegistry,
    extract_ip_from_event,
    make_bucket_name,
)
from kryten_moderator.moderation_list import ModerationEntry


class TestMakeBucketName:
    """Tests for bucket name generation."""

    def test_basic_bucket_name(self):
        """Test basic bucket name creation."""
        name = make_bucket_name("cytu.be", "lounge")
        assert name == "kryten_moderator_ip_map_cytu_be_lounge"


class TestIPManager:
    """Tests for IPManager class."""

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

    @pytest.fixture
    def mock_moderation_list(self):
        """Create a mock ModerationList."""
        mod_list = MagicMock()
        mod_list.check_username = MagicMock(return_value=None)
        return mod_list

    @pytest.mark.asyncio
    async def test_initialize_empty(self, mock_client):
        """Test initializing an empty IP manager."""
        manager = IPManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        
        assert manager._initialized is True
        assert manager.size == 0

    @pytest.mark.asyncio
    async def test_add_ip(self, mock_client):
        """Test adding an IP mapping."""
        manager = IPManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        
        await manager.add_ip("192.168.1.1", "testuser")
        
        assert manager.size == 1
        usernames = manager.get_usernames_for_ip("192.168.1.1")
        assert "testuser" in usernames
        mock_client.kv_put.assert_called()

    @pytest.mark.asyncio
    async def test_add_ip_multiple_users(self, mock_client):
        """Test adding multiple users to same IP."""
        manager = IPManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        
        await manager.add_ip("192.168.1.1", "user1")
        await manager.add_ip("192.168.1.1", "user2")
        
        usernames = manager.get_usernames_for_ip("192.168.1.1")
        assert len(usernames) == 2
        assert "user1" in usernames
        assert "user2" in usernames

    @pytest.mark.asyncio
    async def test_add_ip_duplicate_ignored(self, mock_client):
        """Test that duplicate user-IP pairs are not added twice."""
        manager = IPManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        
        await manager.add_ip("192.168.1.1", "sameuser")
        await manager.add_ip("192.168.1.1", "sameuser")
        
        usernames = manager.get_usernames_for_ip("192.168.1.1")
        assert len(usernames) == 1

    @pytest.mark.asyncio
    async def test_add_ip_empty_ignored(self, mock_client):
        """Test that empty/None IP is ignored."""
        manager = IPManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        
        await manager.add_ip("", "testuser")
        await manager.add_ip(None, "testuser")
        
        assert manager.size == 0

    @pytest.mark.asyncio
    async def test_remove_ip(self, mock_client):
        """Test removing an IP mapping."""
        manager = IPManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        
        await manager.add_ip("192.168.1.1", "user1")
        await manager.add_ip("192.168.1.1", "user2")
        
        await manager.remove_ip("192.168.1.1", "user1")
        
        usernames = manager.get_usernames_for_ip("192.168.1.1")
        assert len(usernames) == 1
        assert "user2" in usernames

    @pytest.mark.asyncio
    async def test_check_ip_correlation_found(self, mock_client, mock_moderation_list):
        """Test IP correlation detection when match found."""
        manager = IPManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        
        # Add a known bad user
        await manager.add_ip("192.168.1.1", "baduser")
        
        # Set up moderation list to return an entry for baduser
        entry = ModerationEntry(
            username="baduser",
            action="smute",
            reason="trolling",
            moderator="admin",
            timestamp="2025-12-14T10:00:00+00:00",
            ips=["192.168.1.1"],
        )
        mock_moderation_list.check_username = MagicMock(
            side_effect=lambda u: entry if u == "baduser" else None
        )
        
        # Check if new user with same IP correlates
        result = manager.check_ip_correlation(
            "192.168.1.1",
            mock_moderation_list,
            exclude_username="newuser",
        )
        
        assert result is not None
        source_username, source_entry = result
        assert source_username == "baduser"
        assert source_entry.action == "smute"

    @pytest.mark.asyncio
    async def test_check_ip_correlation_excludes_self(self, mock_client, mock_moderation_list):
        """Test that IP correlation excludes the user themselves."""
        manager = IPManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        
        await manager.add_ip("192.168.1.1", "sameuser")
        
        entry = ModerationEntry(
            username="sameuser",
            action="smute",
            reason="trolling",
            moderator="admin",
            timestamp="2025-12-14T10:00:00+00:00",
            ips=["192.168.1.1"],
        )
        mock_moderation_list.check_username = MagicMock(return_value=entry)
        
        # Should not match self
        result = manager.check_ip_correlation(
            "192.168.1.1",
            mock_moderation_list,
            exclude_username="sameuser",
        )
        
        assert result is None

    @pytest.mark.asyncio
    async def test_check_ip_correlation_not_found(self, mock_client, mock_moderation_list):
        """Test IP correlation when no match found."""
        manager = IPManager(mock_client, "cytu.be", "lounge")
        await manager.initialize()
        
        await manager.add_ip("192.168.1.1", "gooduser")
        
        # gooduser is not moderated
        mock_moderation_list.check_username = MagicMock(return_value=None)
        
        result = manager.check_ip_correlation(
            "192.168.1.1",
            mock_moderation_list,
        )
        
        assert result is None

    def test_mask_ip(self):
        """Test IP masking for privacy."""
        assert IPManager._mask_ip("192.168.1.42") == "192.168.x.x"
        assert IPManager._mask_ip("10.0.0.1") == "10.0.x.x"
        assert IPManager._mask_ip("") == "(no IP)"
        assert IPManager._mask_ip(None) == "(no IP)"


class TestExtractIpFromEvent:
    """Tests for IP extraction from events."""

    def test_extract_full_ip(self):
        """Test extracting full IP from event."""
        event = MagicMock()
        event.raw = {"meta": {"ip": "192.168.1.1"}}
        
        full, masked = extract_ip_from_event(event)
        
        assert full == "192.168.1.1"

    def test_extract_masked_ip(self):
        """Test extracting masked IP from event."""
        event = MagicMock()
        event.raw = {"meta": {"ip_masked": "192.168.x.x"}}
        
        full, masked = extract_ip_from_event(event)
        
        assert masked == "192.168.x.x"

    def test_extract_no_raw(self):
        """Test extraction when no raw data available."""
        event = MagicMock(spec=[])  # No raw attribute
        
        full, masked = extract_ip_from_event(event)
        
        assert full is None
        assert masked is None

    def test_extract_from_aliases(self):
        """Test extracting IP from aliases fallback."""
        event = MagicMock()
        event.raw = {"meta": {"aliases": ["192.168.1.x"]}}
        
        full, masked = extract_ip_from_event(event)
        
        assert masked == "192.168.1.x"


class TestIPManagerRegistry:
    """Tests for IPManagerRegistry class."""

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
    async def test_get_manager(self, mock_client):
        """Test getting an IP manager for a channel."""
        registry = IPManagerRegistry(mock_client)
        
        manager = await registry.get_manager("cytu.be", "lounge")
        
        assert manager is not None
        assert manager.domain == "cytu.be"
        assert manager.channel == "lounge"

    @pytest.mark.asyncio
    async def test_initialize_all(self, mock_client):
        """Test initializing all channels."""
        registry = IPManagerRegistry(mock_client)
        
        channels = [
            {"domain": "cytu.be", "channel": "lounge"},
            {"domain": "cytu.be", "channel": "movies"},
        ]
        
        await registry.initialize_all(channels)
        
        assert registry.manager_count == 2
