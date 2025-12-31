"""IP correlation management for ban evasion detection.

This module provides the IPManager class for tracking IP-to-username
mappings and detecting when new accounts share IPs with moderated users.
"""

import logging
from typing import TYPE_CHECKING

from kryten import KrytenClient

if TYPE_CHECKING:
    from .moderation_list import ModerationEntry, ModerationList

# KV bucket prefix - actual bucket is per-channel
KV_BUCKET_PREFIX = "kryten_moderator_ip_map"


def make_bucket_name(domain: str, channel: str) -> str:
    """Create a KV bucket name for IP mapping for a specific channel.

    Args:
        domain: CyTube domain (e.g., "cytu.be")
        channel: Channel name

    Returns:
        Bucket name like "kryten_moderator_ip_map_cytu_be_lounge"
    """
    safe_domain = domain.replace(".", "_")
    safe_channel = channel.lower().replace("-", "_")
    return f"{KV_BUCKET_PREFIX}_{safe_domain}_{safe_channel}"


class IPManager:
    """Manages IP-to-username mapping for ban evasion detection.

    Each channel has its own IP map for privacy/isolation.
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
        self._cache: dict[str, list[str]] = {}  # IP -> [usernames]
        self._initialized = False
        self._kv = None  # KV bucket reference

    async def initialize(self) -> None:
        """Initialize KV bucket and load mappings into cache."""
        if self._initialized:
            return

        try:
            # Get or create the bucket - moderator owns this bucket
            self._kv = await self.client.get_or_create_kv_bucket(
                self.bucket_name,
                description=f"Kryten moderator IP mappings for {self.domain}/{self.channel}",
            )

            keys = await self.client.kv_keys(self.bucket_name)

            for key in keys:
                try:
                    data = await self.client.kv_get(self.bucket_name, key, parse_json=True)
                    if data and isinstance(data, list):
                        self._cache[key] = data
                except Exception as e:
                    self.logger.warning(f"Failed to load IP mapping {key}: {e}")

            self._initialized = True
            self.logger.info(
                f"IP manager initialized for {self.domain}/{self.channel}: "
                f"{len(self._cache)} IP mappings loaded"
            )

        except Exception as e:
            self.logger.error(f"Failed to initialize IP manager: {e}")
            raise

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
            await self.client.kv_put(
                self.bucket_name,
                key,
                usernames,
                as_json=True,
            )

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
                await self.client.kv_put(
                    self.bucket_name,
                    key,
                    usernames,
                    as_json=True,
                )
            else:
                await self.client.kv_delete(self.bucket_name, key)

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

    def get_usernames_for_ip(self, ip: str) -> list[str]:
        """Get all usernames associated with an IP.

        Args:
            ip: IP address

        Returns:
            List of usernames
        """
        if not ip:
            return []
        return self._cache.get(ip.lower(), [])

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

        parts = ip.split(".")
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[1]}.x.x"
        return ip

    @property
    def size(self) -> int:
        """Return the number of IP mappings."""
        return len(self._cache)


class IPManagerRegistry:
    """Manages per-channel IP managers.

    Similar to ModerationListManager, this provides unified access
    to IP managers across multiple channels.
    """

    def __init__(self, client: KrytenClient):
        """Initialize with a connected KrytenClient.

        Args:
            client: Connected KrytenClient instance
        """
        self.client = client
        self.logger = logging.getLogger(__name__)
        self._managers: dict[str, IPManager] = {}  # "domain/channel" -> IPManager

    def _make_key(self, domain: str, channel: str) -> str:
        """Create a key for the managers dict."""
        return f"{domain}/{channel}"

    async def get_manager(self, domain: str, channel: str) -> IPManager:
        """Get or create an IP manager for a channel.

        Args:
            domain: CyTube domain
            channel: Channel name

        Returns:
            Initialized IPManager for the channel
        """
        key = self._make_key(domain, channel)

        if key not in self._managers:
            manager = IPManager(self.client, domain, channel)
            await manager.initialize()
            self._managers[key] = manager

        return self._managers[key]

    async def initialize_all(self, channels: list[dict]) -> None:
        """Initialize IP managers for all configured channels.

        Args:
            channels: List of channel configs with 'domain' and 'channel' keys
        """
        for ch in channels:
            domain = ch.get("domain", "cytu.be")
            channel = ch.get("channel")
            if channel:
                await self.get_manager(domain, channel)

        total = sum(m.size for m in self._managers.values())
        self.logger.info(
            f"Initialized {len(self._managers)} IP managers with {total} total mappings"
        )

    def get_manager_sync(self, domain: str, channel: str) -> IPManager | None:
        """Get IP manager synchronously (returns None if not initialized).

        Args:
            domain: CyTube domain
            channel: Channel name

        Returns:
            IPManager if initialized, None otherwise
        """
        key = self._make_key(domain, channel)
        return self._managers.get(key)

    @property
    def total_mappings(self) -> int:
        """Return total IP mappings across all managers."""
        return sum(m.size for m in self._managers.values())

    @property
    def manager_count(self) -> int:
        """Return number of initialized managers."""
        return len(self._managers)


def extract_ip_from_event(event) -> tuple[str | None, str | None]:
    """Extract IP from user join event.

    CyTube may provide IP information in event metadata when available
    to moderators.

    Args:
        event: UserJoinEvent from kryten-py

    Returns:
        Tuple of (full_ip, masked_ip), either may be None
    """
    # Try to get raw event data
    raw = getattr(event, "raw", None)
    if not raw:
        return None, None

    # CyTube stores user metadata in the event
    meta = raw.get("meta", {}) if isinstance(raw, dict) else {}

    # Check for IP in various possible locations
    full_ip = meta.get("ip")
    masked_ip = meta.get("ip_masked")

    # Some CyTube setups put IP in aliases
    if not full_ip and not masked_ip:
        aliases = meta.get("aliases", [])
        if aliases and isinstance(aliases, list):
            # First alias might be IP
            first = aliases[0] if aliases else None
            if first and "." in str(first):
                masked_ip = str(first)

    return full_ip, masked_ip
