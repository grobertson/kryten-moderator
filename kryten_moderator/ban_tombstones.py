"""Ban tombstone tracking for bidirectional ban-list reconciliation.

A "tombstone" records that a ban was intentionally removed, so that a stale
additive ban-list snapshot arriving moments later does not resurrect it. Each
tombstone has an origin ("moderator" or "cytube") and a removal timestamp, and
is expired after a configurable TTL once both sides have converged.

Persisted per-channel in NATS JetStream KV, mirroring ``moderation_list``.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from kryten import KrytenClient, kv_delete, kv_get, kv_keys, kv_put

# Actual bucket is per-channel: kryten_moderator_tombstones_{domain}_{channel}
KV_BUCKET_PREFIX = "kryten_moderator_tombstones"

# Valid tombstone origins
ORIGIN_MODERATOR = "moderator"
ORIGIN_CYTUBE = "cytube"


def make_bucket_name(domain: str, channel: str) -> str:
    """Create a KV bucket name for a specific channel's tombstones."""
    safe_domain = domain.replace(".", "_")
    safe_channel = channel.lower().replace("-", "_")
    return f"{KV_BUCKET_PREFIX}_{safe_domain}_{safe_channel}"


@dataclass
class Tombstone:
    """A record that a ban was intentionally removed.

    Attributes:
        username: The banned username (original case preserved).
        origin: Where the removal originated ("moderator" or "cytube").
        removed_at: ISO 8601 timestamp when the removal was recorded.
    """

    username: str
    origin: str
    removed_at: str

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "Tombstone":
        """Deserialize from JSON string."""
        return cls(**json.loads(data))

    def age_seconds(self, now: datetime | None = None) -> float:
        """Return how many seconds have elapsed since removal."""
        now = now or datetime.now(timezone.utc)
        try:
            ts = datetime.fromisoformat(self.removed_at)
        except ValueError:
            return 0.0
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (now - ts).total_seconds()


class TombstoneList:
    """Manages ban tombstones for a single channel in NATS KV store."""

    def __init__(
        self,
        client: KrytenClient,
        domain: str,
        channel: str,
        ttl_seconds: int = 900,
    ):
        self.client = client
        self.domain = domain
        self.channel = channel
        self.ttl_seconds = ttl_seconds
        self.bucket_name = make_bucket_name(domain, channel)
        self.logger = logging.getLogger(__name__)
        self._cache: dict[str, Tombstone] = {}
        self._initialized = False
        self._kv = None

    async def initialize(self) -> None:
        """Initialize KV bucket, load tombstones, and prune expired ones."""
        if self._initialized:
            return

        self._kv = await self.client.get_or_create_kv_store(
            self.bucket_name,
            description=f"Kryten moderator ban tombstones for {self.domain}/{self.channel}",
        )

        keys = await kv_keys(self._kv)
        for key in keys:
            try:
                data = await kv_get(self._kv, key, parse_json=False)
                if data:
                    json_str = data.decode() if isinstance(data, bytes) else data
                    self._cache[key] = Tombstone.from_json(json_str)
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"Failed to load tombstone {key}: {e}")

        self._initialized = True
        await self.prune()
        self.logger.info(
            f"Tombstones initialized for {self.domain}/{self.channel}: "
            f"{len(self._cache)} active"
        )

    async def add(self, username: str, origin: str) -> Tombstone:
        """Record (or refresh) a tombstone for a removed ban."""
        key = username.lower()
        tombstone = Tombstone(
            username=username,
            origin=origin,
            removed_at=datetime.now(timezone.utc).isoformat(),
        )
        await kv_put(self._kv, key, tombstone.to_json())
        self._cache[key] = tombstone
        self.logger.debug(f"Tombstoned {username} (origin={origin}) in {self.channel}")
        return tombstone

    async def remove(self, username: str) -> bool:
        """Delete a tombstone (both sides have converged)."""
        key = username.lower()
        if key not in self._cache:
            return False
        try:
            await kv_delete(self._kv, key)
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Could not delete tombstone {username}: {e}")
        del self._cache[key]
        return True

    def get(self, username: str) -> Tombstone | None:
        """Return an active (non-expired) tombstone for a user, if any."""
        tombstone = self._cache.get(username.lower())
        if tombstone is None:
            return None
        if tombstone.age_seconds() > self.ttl_seconds:
            return None
        return tombstone

    async def prune(self) -> int:
        """Remove tombstones older than the TTL. Returns the count pruned."""
        expired = [
            key
            for key, ts in self._cache.items()
            if ts.age_seconds() > self.ttl_seconds
        ]
        for key in expired:
            try:
                await kv_delete(self._kv, key)
            except Exception as e:  # noqa: BLE001
                self.logger.debug(f"Could not prune tombstone {key}: {e}")
            del self._cache[key]
        if expired:
            self.logger.debug(f"Pruned {len(expired)} expired tombstone(s) in {self.channel}")
        return len(expired)


class TombstoneManager:
    """Manages per-channel tombstone lists."""

    def __init__(self, client: KrytenClient, ttl_seconds: int = 900):
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.logger = logging.getLogger(__name__)
        self._lists: dict[str, TombstoneList] = {}

    def _make_key(self, domain: str, channel: str) -> str:
        return f"{domain}/{channel}"

    async def get_list(self, domain: str, channel: str) -> TombstoneList:
        """Get or create a tombstone list for a channel."""
        key = self._make_key(domain, channel)
        if key not in self._lists:
            tombstones = TombstoneList(self.client, domain, channel, self.ttl_seconds)
            await tombstones.initialize()
            self._lists[key] = tombstones
        return self._lists[key]
