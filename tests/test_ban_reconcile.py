"""Unit tests for bidirectional ban-list reconciliation in ModeratorService._handle_banlist_event."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kryten_moderator.ban_tombstones import ORIGIN_MODERATOR, TombstoneManager
from kryten_moderator.moderation_list import ModerationListManager
from kryten_moderator.service import ModeratorService


@pytest.fixture
def mock_kv():
    kv = MagicMock()
    kv.keys = AsyncMock(return_value=[])
    kv.get = AsyncMock(return_value=None)
    kv.put = AsyncMock()
    kv.delete = AsyncMock()
    return kv


@pytest.fixture
def mock_client(mock_kv):
    client = MagicMock()
    client.get_or_create_kv_store = AsyncMock(return_value=mock_kv)
    return client


@pytest.fixture
def service(mock_client):
    """A ModeratorService with reconcile dependencies wired to mocks."""
    svc = object.__new__(ModeratorService)
    svc.logger = logging.getLogger("test.reconcile")
    svc._domain = "cytu.be"
    svc.client = AsyncMock()
    svc.moderation_lists = ModerationListManager(mock_client)
    svc.tombstones = TombstoneManager(mock_client, ttl_seconds=900)
    svc._push_ban_to_cytube = AsyncMock()
    svc._unban_on_cytube = AsyncMock()
    svc._get_online_users = AsyncMock(return_value=[])
    svc._enforce_moderation = AsyncMock()
    svc._emit_event = AsyncMock()
    svc._bans_enforced = 0
    return svc


def _event(payload):
    return SimpleNamespace(domain="cytu.be", channel="lounge", payload=payload)


@pytest.mark.asyncio
async def test_cytube_origin_ban_is_imported(service):
    payload = [{"id": 1, "name": "NewBan", "ip": "1.2.3.4", "reason": "x", "bannedby": "admin"}]

    await service._handle_banlist_event(_event(payload))

    mod_list = await service.moderation_lists.get_list("cytu.be", "lounge")
    entry = await mod_list.get("NewBan")
    assert entry is not None
    assert entry.action == "ban"
    assert entry.cytube_seen is True


@pytest.mark.asyncio
async def test_cytube_side_removal_deletes_moderator_entry(service):
    mod_list = await service.moderation_lists.get_list("cytu.be", "lounge")
    await mod_list.add(username="Old", action="ban", moderator="admin", cytube_seen=True)

    # Cytube no longer lists the ban.
    await service._handle_banlist_event(_event([]))

    assert await mod_list.get("Old") is None
    # A cytube-origin tombstone should guard against stale re-import.
    tombstones = await service.tombstones.get_list("cytu.be", "lounge")
    assert tombstones.get("old") is not None


@pytest.mark.asyncio
async def test_moderator_removal_race_suppresses_reimport(service):
    tombstones = await service.tombstones.get_list("cytu.be", "lounge")
    await tombstones.add("Troll", ORIGIN_MODERATOR)

    # Stale snapshot still lists the ban the moderator just removed.
    payload = [{"id": 2, "name": "Troll", "ip": "*", "reason": "", "bannedby": "mod"}]
    await service._handle_banlist_event(_event(payload))

    mod_list = await service.moderation_lists.get_list("cytu.be", "lounge")
    assert await mod_list.get("Troll") is None  # NOT resurrected
    service._unban_on_cytube.assert_awaited_once()


@pytest.mark.asyncio
async def test_unenforced_moderator_ban_is_pushed(service):
    mod_list = await service.moderation_lists.get_list("cytu.be", "lounge")
    await mod_list.add(username="Fresh", action="ban", moderator="admin", cytube_seen=False)

    # Not yet present on Cytube.
    await service._handle_banlist_event(_event([]))

    service._push_ban_to_cytube.assert_awaited_once()
    assert await mod_list.get("Fresh") is not None  # kept until confirmed


@pytest.mark.asyncio
async def test_in_sync_marks_cytube_seen(service):
    mod_list = await service.moderation_lists.get_list("cytu.be", "lounge")
    await mod_list.add(username="Sync", action="ban", moderator="admin", cytube_seen=False)

    payload = [{"id": 3, "name": "Sync", "ip": "*", "reason": "", "bannedby": "mod"}]
    await service._handle_banlist_event(_event(payload))

    entry = await mod_list.get("Sync")
    assert entry is not None
    assert entry.cytube_seen is True
    service._push_ban_to_cytube.assert_not_awaited()
    service._unban_on_cytube.assert_not_awaited()


@pytest.mark.asyncio
async def test_mutes_are_ignored_by_reconcile(service):
    mod_list = await service.moderation_lists.get_list("cytu.be", "lounge")
    await mod_list.add(username="Muted", action="smute", moderator="admin")

    # Empty Cytube ban list must NOT touch a smute entry.
    await service._handle_banlist_event(_event([]))

    assert await mod_list.get("Muted") is not None
    service._push_ban_to_cytube.assert_not_awaited()
