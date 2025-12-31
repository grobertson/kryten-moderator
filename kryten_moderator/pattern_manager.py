"""Username pattern matching for automatic moderation.

This module provides pattern-based blocking of offensive or known-bad
usernames. Patterns can be simple substrings or regular expressions.
"""

import base64
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

from kryten import KrytenClient

# Action types for moderation
ActionType = Literal["ban", "smute", "mute"]

# KV bucket prefix - actual bucket is per-channel
KV_BUCKET_PREFIX = "kryten_moderator_patterns"


def make_bucket_name(domain: str, channel: str) -> str:
    """Create a KV bucket name for patterns for a specific channel.

    Args:
        domain: CyTube domain (e.g., "cytu.be")
        channel: Channel name

    Returns:
        Bucket name like "kryten_moderator_patterns_cytu_be_lounge"
    """
    safe_domain = domain.replace(".", "_")
    safe_channel = channel.lower().replace("-", "_")
    return f"{KV_BUCKET_PREFIX}_{safe_domain}_{safe_channel}"


@dataclass
class PatternEntry:
    """A banned username pattern.

    Attributes:
        pattern: The pattern string (substring or regex)
        is_regex: True if pattern is a regex, False for substring
        action: "ban", "smute", or "mute"
        added_by: Who added this pattern
        timestamp: ISO 8601 timestamp when added
        description: Optional description of what this pattern blocks
    """

    pattern: str
    is_regex: bool
    action: ActionType
    added_by: str
    timestamp: str
    description: str | None = None

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, data: str) -> "PatternEntry":
        """Deserialize from JSON string."""
        return cls(**json.loads(data))

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return asdict(self)


class CompiledPattern:
    """A pattern with compiled regex for fast matching."""

    def __init__(self, entry: PatternEntry):
        """Initialize with a PatternEntry.

        Args:
            entry: The pattern entry to compile

        Raises:
            ValueError: If the regex is invalid
        """
        self.entry = entry
        self._regex: re.Pattern | None = None
        self._compile()

    def _compile(self) -> None:
        """Compile the pattern for matching."""
        if self.entry.is_regex:
            try:
                self._regex = re.compile(self.entry.pattern, re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{self.entry.pattern}': {e}")
        else:
            # For substring, create a regex that matches anywhere
            escaped = re.escape(self.entry.pattern)
            self._regex = re.compile(escaped, re.IGNORECASE)

    def matches(self, username: str) -> bool:
        """Check if username matches this pattern.

        Args:
            username: Username to check

        Returns:
            True if username matches the pattern
        """
        if self._regex:
            return bool(self._regex.search(username))
        return False


class PatternManager:
    """Manages banned username patterns for a channel.

    Each channel has its own pattern list for flexibility.
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
        self._patterns: dict[str, CompiledPattern] = {}  # key -> CompiledPattern
        self._initialized = False
        self._kv = None  # KV bucket reference

    @staticmethod
    def _make_key(pattern: str) -> str:
        """Create a safe KV key from pattern string.

        Some patterns may contain special characters that aren't valid in keys.

        Args:
            pattern: Pattern string

        Returns:
            URL-safe base64 encoded key
        """
        return base64.urlsafe_b64encode(pattern.encode()).decode()

    @staticmethod
    def _decode_key(key: str) -> str:
        """Decode a KV key back to pattern string.

        Args:
            key: URL-safe base64 encoded key

        Returns:
            Original pattern string
        """
        try:
            return base64.urlsafe_b64decode(key.encode()).decode()
        except Exception:
            return key  # Return as-is if decoding fails

    async def initialize(self, default_patterns: list | None = None) -> None:
        """Initialize KV bucket and load patterns.

        Args:
            default_patterns: Optional list of default patterns to add if bucket is empty.
                              Can be strings (simple patterns) or dicts with full config.
        """
        if self._initialized:
            return

        try:
            # Get or create the bucket - moderator owns this bucket
            self._kv = await self.client.get_or_create_kv_bucket(
                self.bucket_name,
                description=f"Kryten moderator patterns for {self.domain}/{self.channel}",
            )

            keys = await self.client.kv_keys(self.bucket_name)

            for key in keys:
                try:
                    data = await self.client.kv_get(self.bucket_name, key, parse_json=False)
                    if data:
                        json_str = data.decode() if isinstance(data, bytes) else data
                        pattern_entry = PatternEntry.from_json(json_str)
                        try:
                            self._patterns[key] = CompiledPattern(pattern_entry)
                        except ValueError as e:
                            self.logger.warning(f"Skipping invalid pattern: {e}")
                except Exception as e:
                    self.logger.warning(f"Failed to load pattern {key}: {e}")

            self._initialized = True
            self.logger.info(
                f"Pattern manager initialized for {self.domain}/{self.channel}: "
                f"{len(self._patterns)} patterns loaded"
            )

            # Add default patterns if bucket was empty
            if not self._patterns and default_patterns:
                await self._add_default_patterns(default_patterns)

        except Exception as e:
            self.logger.error(f"Failed to initialize pattern manager: {e}")
            raise

    async def _add_default_patterns(self, patterns: list) -> None:
        """Add default patterns on first startup.

        Args:
            patterns: List of pattern configs - strings or dicts
        """
        self.logger.info(f"Adding {len(patterns)} default patterns")

        for p in patterns:
            try:
                if isinstance(p, str):
                    # Simple string pattern
                    await self.add(
                        pattern=p,
                        is_regex=False,
                        action="ban",
                        added_by="system:default",
                        description="Default pattern",
                    )
                elif isinstance(p, dict):
                    # Full pattern config
                    await self.add(
                        pattern=p["pattern"],
                        is_regex=p.get("is_regex", False),
                        action=p.get("action", "ban"),
                        added_by="system:default",
                        description=p.get("description"),
                    )
            except Exception as e:
                self.logger.warning(f"Failed to add default pattern {p}: {e}")

    async def add(
        self,
        pattern: str,
        is_regex: bool,
        action: ActionType,
        added_by: str,
        description: str | None = None,
    ) -> PatternEntry:
        """Add a pattern to the banned list.

        Args:
            pattern: Pattern string (substring or regex)
            is_regex: True if pattern is a regex
            action: Action to take on match (ban/smute/mute)
            added_by: Who is adding this pattern
            description: Optional description

        Returns:
            The created PatternEntry

        Raises:
            ValueError: If regex is invalid
        """
        entry = PatternEntry(
            pattern=pattern,
            is_regex=is_regex,
            action=action,
            added_by=added_by,
            timestamp=datetime.now(timezone.utc).isoformat(),
            description=description,
        )

        # Validate by compiling
        compiled = CompiledPattern(entry)

        # Store in KV
        key = self._make_key(pattern)
        await self.client.kv_put(
            self.bucket_name,
            key,
            entry.to_json(),
            as_json=False,
        )

        # Update cache
        self._patterns[key] = compiled

        self.logger.info(
            f"Added pattern: '{pattern}' (regex={is_regex}, action={action}) " f"for {self.channel}"
        )
        return entry

    async def remove(self, pattern: str) -> bool:
        """Remove a pattern from the banned list.

        Args:
            pattern: Pattern string to remove

        Returns:
            True if removed, False if not found
        """
        key = self._make_key(pattern)

        if key not in self._patterns:
            return False

        await self.client.kv_delete(self.bucket_name, key)
        del self._patterns[key]

        self.logger.info(f"Removed pattern: '{pattern}' from {self.channel}")
        return True

    async def get(self, pattern: str) -> PatternEntry | None:
        """Get a specific pattern entry.

        Args:
            pattern: Pattern string to look up

        Returns:
            PatternEntry if found, None otherwise
        """
        key = self._make_key(pattern)
        compiled = self._patterns.get(key)
        return compiled.entry if compiled else None

    async def list_all(self) -> list[PatternEntry]:
        """List all patterns.

        Returns:
            List of PatternEntry objects, sorted by timestamp (newest first)
        """
        entries = [cp.entry for cp in self._patterns.values()]
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries

    def check_username(self, username: str) -> tuple[PatternEntry, str] | None:
        """Check if username matches any banned pattern.

        This is the hot path for join-time matching.

        Args:
            username: Username to check

        Returns:
            (PatternEntry, matched_pattern) if match found, None otherwise
        """
        for compiled in self._patterns.values():
            if compiled.matches(username):
                return (compiled.entry, compiled.entry.pattern)

        return None

    @property
    def count(self) -> int:
        """Return the number of patterns."""
        return len(self._patterns)


class PatternManagerRegistry:
    """Manages per-channel pattern managers.

    Similar to other registries, this provides unified access
    to pattern managers across multiple channels.
    """

    def __init__(self, client: KrytenClient):
        """Initialize with a connected KrytenClient.

        Args:
            client: Connected KrytenClient instance
        """
        self.client = client
        self.logger = logging.getLogger(__name__)
        self._managers: dict[str, PatternManager] = {}  # "domain/channel" -> PatternManager

    def _make_key(self, domain: str, channel: str) -> str:
        """Create a key for the managers dict."""
        return f"{domain}/{channel}"

    async def get_manager(
        self, domain: str, channel: str, default_patterns: list | None = None
    ) -> PatternManager:
        """Get or create a pattern manager for a channel.

        Args:
            domain: CyTube domain
            channel: Channel name
            default_patterns: Default patterns to add if bucket is empty

        Returns:
            Initialized PatternManager for the channel
        """
        key = self._make_key(domain, channel)

        if key not in self._managers:
            manager = PatternManager(self.client, domain, channel)
            await manager.initialize(default_patterns)
            self._managers[key] = manager

        return self._managers[key]

    async def initialize_all(
        self, channels: list[dict], default_patterns: list | None = None
    ) -> None:
        """Initialize pattern managers for all configured channels.

        Args:
            channels: List of channel configs with 'domain' and 'channel' keys
            default_patterns: Default patterns to add to each channel
        """
        for ch in channels:
            domain = ch.get("domain", "cytu.be")
            channel = ch.get("channel")
            if channel:
                await self.get_manager(domain, channel, default_patterns)

        total = sum(m.count for m in self._managers.values())
        self.logger.info(
            f"Initialized {len(self._managers)} pattern managers with {total} total patterns"
        )

    def get_manager_sync(self, domain: str, channel: str) -> PatternManager | None:
        """Get pattern manager synchronously (returns None if not initialized).

        Args:
            domain: CyTube domain
            channel: Channel name

        Returns:
            PatternManager if initialized, None otherwise
        """
        key = self._make_key(domain, channel)
        return self._managers.get(key)

    @property
    def total_patterns(self) -> int:
        """Return total patterns across all managers."""
        return sum(m.count for m in self._managers.values())

    @property
    def manager_count(self) -> int:
        """Return number of initialized managers."""
        return len(self._managers)
