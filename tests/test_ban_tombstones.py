"""Unit tests for the ban_tombstones module."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from kryten_moderator.ban_tombstones import (
    ORIGIN_CYTUBE,
    ORIGIN_MODERATOR,
    Tombstone,
    TombstoneList,
    make_bucket_name,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestTombstone:
    def test_json_round_trip(self):
        ts = Tombstone(username="Bad", origin=ORIGIN_MODERATOR, removed_at=_iso(datetime.now(timezone.utc)))
        restored = Tombstone.from_json(ts.to_json())
        assert restored == ts

    def test_age_seconds(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=120)
        ts = Tombstone(username="x", origin=ORIGIN_CYTUBE, removed_at=_iso(past))
        assert ts.age_seconds() >= 119

    def test_bucket_name(self):
        assert make_bucket_name("cytu.be", "Channel-Z") == (
            "kryten_moderator_tombstones_cytu_be_channel_z"
        )


class TestTombstoneList:
    @pytest.fixture
    def mock_kv(self):
        kv = MagicMock()
        kv.keys = AsyncMock(return_value=[])
        kv.get = AsyncMock(return_value=None)
        kv.put = AsyncMock()
        kv.delete = AsyncMock()
        return kv

    @pytest.fixture
    def mock_client(self, mock_kv):
        client = MagicMock()
        client.get_or_create_kv_store = AsyncMock(return_value=mock_kv)
        return client

    @pytest.mark.asyncio
    async def test_add_and_get(self, mock_client, mock_kv):
        tl = TombstoneList(mock_client, "cytu.be", "lounge", ttl_seconds=900)
        await tl.initialize()

        await tl.add("Troll", ORIGIN_MODERATOR)

        got = tl.get("troll")  # case-insensitive
        assert got is not None
        assert got.origin == ORIGIN_MODERATOR
        mock_kv.put.assert_called()

    @pytest.mark.asyncio
    async def test_remove(self, mock_client, mock_kv):
        tl = TombstoneList(mock_client, "cytu.be", "lounge", ttl_seconds=900)
        await tl.initialize()
        await tl.add("Troll", ORIGIN_MODERATOR)

        assert await tl.remove("troll") is True
        assert tl.get("troll") is None
        mock_kv.delete.assert_called()

    @pytest.mark.asyncio
    async def test_expired_tombstone_not_returned(self, mock_client):
        tl = TombstoneList(mock_client, "cytu.be", "lounge", ttl_seconds=60)
        await tl.initialize()
        # Inject an old tombstone directly into cache.
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        tl._cache["ghost"] = Tombstone("ghost", ORIGIN_MODERATOR, _iso(old))

        assert tl.get("ghost") is None

    @pytest.mark.asyncio
    async def test_prune_removes_expired(self, mock_client, mock_kv):
        tl = TombstoneList(mock_client, "cytu.be", "lounge", ttl_seconds=60)
        await tl.initialize()
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        fresh = datetime.now(timezone.utc)
        tl._cache["old"] = Tombstone("old", ORIGIN_CYTUBE, _iso(old))
        tl._cache["new"] = Tombstone("new", ORIGIN_MODERATOR, _iso(fresh))

        pruned = await tl.prune()

        assert pruned == 1
        assert "old" not in tl._cache
        assert "new" in tl._cache

    @pytest.mark.asyncio
    async def test_initialize_loads_and_prunes(self, mock_client, mock_kv):
        old = datetime.now(timezone.utc) - timedelta(seconds=999)
        fresh = datetime.now(timezone.utc)
        stored = {
            "old": Tombstone("old", ORIGIN_CYTUBE, _iso(old)).to_json(),
            "keep": Tombstone("keep", ORIGIN_MODERATOR, _iso(fresh)).to_json(),
        }
        mock_kv.keys = AsyncMock(return_value=list(stored.keys()))

        def _entry(raw: bytes):
            m = MagicMock()
            m.value = raw
            return m

        mock_kv.get = AsyncMock(side_effect=lambda key: _entry(stored[key].encode()))

        tl = TombstoneList(mock_client, "cytu.be", "lounge", ttl_seconds=60)
        await tl.initialize()

        assert tl.get("keep") is not None
        assert tl.get("old") is None
