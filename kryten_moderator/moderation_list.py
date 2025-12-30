"""Moderation list management for persistent user moderation.

This module provides the ModerationEntry data model and ModerationList class
for managing banned/muted users with persistence via NATS JetStream KV store.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

from kryten import KrytenClient

# Action types for moderation
ActionType = Literal["ban", "smute", "mute"]

# KV bucket name prefix - actual bucket is per-channel: kryten_moderator_entries_{domain}_{channel}
KV_BUCKET_PREFIX = "kryten_moderator_entries"


def make_bucket_name(domain: str, channel: str) -> str:
    """Create a KV bucket name for a specific channel.

    Args:
        domain: CyTube domain (e.g., "cytu.be")
        channel: Channel name

    Returns:
        Bucket name like "kryten_moderator_entries_cytu_be_lounge"
    """
    safe_domain = domain.replace(".", "_")
    safe_channel = channel.lower().replace("-", "_")
    return f"{KV_BUCKET_PREFIX}_{safe_domain}_{safe_channel}"


@dataclass
class ModerationEntry:
    """A moderation entry for a user.

    Attributes:
        username: The username (original case preserved)
        action: "ban", "smute", or "mute"
        reason: Optional reason for the moderation
        moderator: Who added this entry
        timestamp: ISO 8601 timestamp when added
        ips: List of known IP addresses for this user
        ip_correlation_source: Original username if this was auto-added via IP correlation
        pattern_match: Pattern that matched if this was auto-added via pattern matching
    """
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

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return asdict(self)


class ModerationList:
    """Manages the moderation list in NATS KV store.

    This class provides an in-memory cache for fast lookups during
    user join events, backed by persistent NATS JetStream KV storage.

    Each channel has its own separate moderation list.
    """

    def __init__(self, client: KrytenClient, domain: str, channel: str):
        """Initialize with a connected KrytenClient.

        Args:
            client: Connected KrytenClient instance
            domain: CyTube domain (e.g., "cytu.be")
            channel: Channel name
        """
        self.client = client
        self.domain = domain
        self.channel = channel
        self.bucket_name = make_bucket_name(domain, channel)
        self.logger = logging.getLogger(__name__)
        self._cache: dict[str, ModerationEntry] = {}
        self._initialized = False
        self._kv = None  # KV bucket reference

    async def initialize(self) -> None:
        """Initialize KV bucket and load entries into cache."""
        if self._initialized:
            return

        try:
            # Get or create the bucket - moderator owns this bucket
            self._kv = await self.client.get_or_create_kv_bucket(
                self.bucket_name,
                description=f"Kryten moderator entries for {self.domain}/{self.channel}",
            )

            # Load all entries into cache
            keys = await self.client.kv_keys(self.bucket_name)
            for key in keys:
                try:
                    data = await self.client.kv_get(
                        self.bucket_name, key, parse_json=False
                    )
                    if data:
                        # kv_get returns bytes when parse_json=False
                        json_str = data.decode() if isinstance(data, bytes) else data
                        self._cache[key] = ModerationEntry.from_json(json_str)
                except Exception as e:
                    self.logger.warning(f"Failed to load entry {key}: {e}")

            self._initialized = True
            self.logger.info(
                f"Moderation list initialized for {self.domain}/{self.channel}: "
                f"{len(self._cache)} entries loaded"
            )

        except Exception as e:
            self.logger.error(f"Failed to initialize moderation list: {e}")
            raise

    async def add(
        self,
        username: str,
        action: ActionType,
        moderator: str,
        reason: str | None = None,
        ips: list[str] | None = None,
        ip_correlation_source: str | None = None,
        pattern_match: str | None = None,
    ) -> ModerationEntry:
        """Add a user to the moderation list.

        Args:
            username: Username to moderate (case-insensitive for lookup)
            action: "ban", "smute", or "mute"
            moderator: Who is adding this entry
            reason: Optional reason for the action
            ips: Optional list of known IPs
            ip_correlation_source: Set if auto-added via IP correlation
            pattern_match: Set if auto-added via pattern matching

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
            ips=ips or [],
            ip_correlation_source=ip_correlation_source,
            pattern_match=pattern_match,
        )

        # Store in KV
        await self.client.kv_put(
            self.bucket_name,
            key,
            entry.to_json(),
            as_json=False,  # Already JSON string
        )

        # Update cache
        self._cache[key] = entry

        self.logger.info(
            f"Added moderation entry: {action} {username} by {moderator} "
            f"(channel: {self.channel})"
        )
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
        await self.client.kv_delete(self.bucket_name, key)

        # Update cache
        del self._cache[key]

        self.logger.info(
            f"Removed moderation entry for: {username} (channel: {self.channel})"
        )
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
            List of ModerationEntry objects, sorted by timestamp (newest first)
        """
        entries = list(self._cache.values())

        if filter_action:
            entries = [e for e in entries if e.action == filter_action]

        # Sort by timestamp descending (most recent first)
        entries.sort(key=lambda e: e.timestamp, reverse=True)

        return entries

    def check_username(self, username: str) -> ModerationEntry | None:
        """Check if username is in moderation list (fast, cache-only).

        This is the hot path for join enforcement - no async/await needed.

        Args:
            username: Username to check (case-insensitive)

        Returns:
            ModerationEntry if found, None otherwise
        """
        return self._cache.get(username.lower())

    async def update_ips(self, username: str, ip: str) -> bool:
        """Add an IP to a user's moderation entry.

        Args:
            username: Username to update
            ip: IP address to add

        Returns:
            True if updated, False if user not in list
        """
        key = username.lower()
        entry = self._cache.get(key)

        if not entry:
            return False

        if ip not in entry.ips:
            entry.ips.append(ip)

            # Persist update
            await self.client.kv_put(
                self.bucket_name,
                key,
                entry.to_json(),
                as_json=False,
            )

            self.logger.debug(f"Added IP {ip} to entry for {username}")

        return True

    @property
    def size(self) -> int:
        """Return the number of entries in the moderation list."""
        return len(self._cache)

    @property
    def channel_key(self) -> str:
        """Return the channel key (domain/channel) for this list."""
        return f"{self.domain}/{self.channel}"


class ModerationListManager:
    """Manages multiple per-channel moderation lists.

    This class provides a unified interface for accessing moderation lists
    across multiple configured channels.
    """

    def __init__(self, client: KrytenClient):
        """Initialize with a connected KrytenClient.

        Args:
            client: Connected KrytenClient instance
        """
        self.client = client
        self.logger = logging.getLogger(__name__)
        self._lists: dict[str, ModerationList] = {}  # "domain/channel" -> ModerationList

    def _make_key(self, domain: str, channel: str) -> str:
        """Create a key for the lists dict."""
        return f"{domain}/{channel}"

    async def get_list(self, domain: str, channel: str) -> ModerationList:
        """Get or create a moderation list for a channel.

        Args:
            domain: CyTube domain
            channel: Channel name

        Returns:
            Initialized ModerationList for the channel
        """
        key = self._make_key(domain, channel)

        if key not in self._lists:
            mod_list = ModerationList(self.client, domain, channel)
            await mod_list.initialize()
            self._lists[key] = mod_list

        return self._lists[key]

    async def initialize_all(self, channels: list[dict]) -> None:
        """Initialize moderation lists for all configured channels.

        Args:
            channels: List of channel configs with 'domain' and 'channel' keys
        """
        for ch in channels:
            domain = ch.get("domain", "cytu.be")
            channel = ch.get("channel")
            if channel:
                await self.get_list(domain, channel)

        total = sum(ml.size for ml in self._lists.values())
        self.logger.info(
            f"Initialized {len(self._lists)} moderation lists with {total} total entries"
        )

    def check_username(self, domain: str, channel: str, username: str) -> ModerationEntry | None:
        """Check if username is in a channel's moderation list.

        Fast synchronous lookup - assumes list is already initialized.

        Args:
            domain: CyTube domain
            channel: Channel name
            username: Username to check

        Returns:
            ModerationEntry if found, None otherwise
        """
        key = self._make_key(domain, channel)
        mod_list = self._lists.get(key)

        if mod_list:
            return mod_list.check_username(username)

        return None

    @property
    def total_entries(self) -> int:
        """Return total entries across all lists."""
        return sum(ml.size for ml in self._lists.values())

    @property
    def list_count(self) -> int:
        """Return number of initialized lists."""
        return len(self._lists)
