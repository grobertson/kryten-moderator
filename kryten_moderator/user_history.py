"""User connection history tracker.

Maintains a rolling in-memory window (up to ``history_retention_hours``,
default 12) of user connection events per channel.  Designed specifically
to support detection of "driveby" accounts that join briefly, act, and leave
before a moderator can respond.

Data model
----------
Each user gets one ``UserRecord`` per ``(domain, channel)`` containing an
ordered list of ``Session`` objects.  A session opens on *adduser* (join)
and closes on *userleave*.  Chat messages increment the active session's
``message_count``.

Storage
-------
All data is in-memory only and is lost on service restart.  Timestamps are
UTC Unix epoch floats; IPs are stored in masked form (e.g. ``1.2.3.x``).
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

MAX_RETENTION_SECONDS: float = 12 * 3600   # 12 hours
DEFAULT_WINDOW_SECONDS: float = 3600        # 1 hour


def _ts(t: float) -> str:
    """Format a Unix timestamp as a UTC ISO-8601 string."""
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


@dataclass
class Session:
    """A single contiguous visit by a user to a channel."""

    joined_at: float                    # Unix timestamp (UTC)
    left_at: float | None = None        # None means user is still present
    ip: str | None = None               # Masked IP, e.g. "1.2.3.x"
    message_count: int = 0

    @property
    def is_active(self) -> bool:
        """True while the session has no recorded departure."""
        return self.left_at is None

    @property
    def duration_seconds(self) -> float | None:
        """Session length in seconds; ``None`` if the session is still open."""
        if self.left_at is None:
            return None
        return max(0.0, self.left_at - self.joined_at)

    def to_dict(self) -> dict:
        return {
            "joined_at": _ts(self.joined_at),
            "left_at": _ts(self.left_at) if self.left_at is not None else None,
            "duration_seconds": self.duration_seconds,
            "ip": self.ip,
            "message_count": self.message_count,
        }


@dataclass
class UserRecord:
    """Aggregated history for a single user within a single channel."""

    username: str
    sessions: list[Session] = field(default_factory=list)

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    @property
    def total_messages(self) -> int:
        return sum(s.message_count for s in self.sessions)

    @property
    def last_seen(self) -> float | None:
        """Most recent activity timestamp (``left_at`` or ``joined_at`` of the last session)."""
        if not self.sessions:
            return None
        last = self.sessions[-1]
        return last.left_at if last.left_at is not None else last.joined_at

    @property
    def active_session(self) -> Session | None:
        """Return the most recent open session, or ``None`` if all are closed."""
        for s in reversed(self.sessions):
            if s.is_active:
                return s
        return None

    def to_dict(self, moderation_action: str | None = None) -> dict:
        last = self.last_seen
        return {
            "username": self.username,
            "moderation_action": moderation_action,
            "session_count": self.session_count,
            "total_messages": self.total_messages,
            "last_seen": _ts(last) if last is not None else None,
            "sessions": [s.to_dict() for s in self.sessions],
        }


class UserHistoryManager:
    """Tracks connection history for all users in a single ``(domain, channel)``.

    Designed for single-threaded asyncio use — no locking is performed.
    """

    def __init__(
        self,
        domain: str,
        channel: str,
        retention_seconds: float = MAX_RETENTION_SECONDS,
    ) -> None:
        self.domain = domain
        self.channel = channel
        self.retention_seconds = retention_seconds
        # Keyed by lowercased username to handle case-insensitive CyTube usernames.
        self._records: dict[str, UserRecord] = {}

    # ------------------------------------------------------------------
    # Event feeds (called by service.py event handlers)
    # ------------------------------------------------------------------

    def on_join(self, username: str, ip: str | None = None) -> None:
        """Open a new session for a joining user."""
        record = self._get_or_create(username)
        record.sessions.append(Session(joined_at=time.time(), ip=ip))

    def on_leave(self, username: str) -> None:
        """Close the most recent active session for a departing user."""
        record = self._records.get(username.lower())
        if record is None:
            return
        active = record.active_session
        if active is not None:
            active.left_at = time.time()

    def on_message(self, username: str) -> None:
        """Increment the message count on the user's active session.

        If no join event was seen (service started mid-session) a synthetic
        session is created so message counts are still captured.
        """
        key = username.lower()
        if key not in self._records:
            # Synthetic session — bot started while user was already present.
            record = UserRecord(username=username)
            record.sessions.append(Session(joined_at=time.time()))
            self._records[key] = record

        record = self._records[key]
        target = record.active_session
        if target is None and record.sessions:
            # Race: message arrived after leave event; credit the last session.
            target = record.sessions[-1]
        if target is not None:
            target.message_count += 1

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        window_seconds: float,
        now: float | None = None,
    ) -> list[UserRecord]:
        """Return ``UserRecord`` objects with activity within ``window_seconds``.

        Lazily prunes expired data before filtering.  Active sessions (no
        ``left_at``) are always included regardless of window.

        Args:
            window_seconds: How far back to look; capped at ``retention_seconds``.
            now: Override for current time (useful in tests).

        Returns:
            List sorted by ``last_seen`` descending (most recent first).
        """
        if now is None:
            now = time.time()
        self._prune(now)
        cutoff = now - min(window_seconds, self.retention_seconds)

        results: list[UserRecord] = []
        for record in self._records.values():
            windowed = [
                s for s in record.sessions
                if s.is_active or (s.left_at is not None and s.left_at >= cutoff)
            ]
            if windowed:
                results.append(UserRecord(username=record.username, sessions=windowed))

        results.sort(key=lambda r: r.last_seen or 0.0, reverse=True)
        return results

    @property
    def user_count(self) -> int:
        """Number of users currently tracked (before next prune)."""
        return len(self._records)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_or_create(self, username: str) -> UserRecord:
        key = username.lower()
        if key not in self._records:
            self._records[key] = UserRecord(username=username)
        return self._records[key]

    def _prune(self, now: float) -> None:
        """Remove sessions older than ``retention_seconds``, and empty records."""
        cutoff = now - self.retention_seconds
        to_delete: list[str] = []
        for key, record in self._records.items():
            record.sessions = [
                s for s in record.sessions
                if s.is_active or (s.left_at is not None and s.left_at >= cutoff)
            ]
            if not record.sessions:
                to_delete.append(key)
        for key in to_delete:
            del self._records[key]


class UserHistoryRegistry:
    """Registry of per-channel ``UserHistoryManager`` instances."""

    def __init__(self, retention_seconds: float = MAX_RETENTION_SECONDS) -> None:
        self.retention_seconds = retention_seconds
        self._managers: dict[str, UserHistoryManager] = {}

    def initialize_all(self, channels: list[dict]) -> None:
        """Pre-create a manager for every configured channel."""
        for ch in channels:
            domain = ch.get("domain", "cytu.be")
            channel = ch.get("channel", "")
            if channel:
                self._ensure(domain, channel)

    def get_manager(self, domain: str, channel: str) -> UserHistoryManager | None:
        return self._managers.get(f"{domain}/{channel}")

    # ------------------------------------------------------------------
    # Event routing — called by service.py
    # ------------------------------------------------------------------

    def on_join(self, domain: str, channel: str, username: str, ip: str | None = None) -> None:
        self._ensure(domain, channel).on_join(username, ip)

    def on_leave(self, domain: str, channel: str, username: str) -> None:
        mgr = self.get_manager(domain, channel)
        if mgr is not None:
            mgr.on_leave(username)

    def on_message(self, domain: str, channel: str, username: str) -> None:
        mgr = self.get_manager(domain, channel)
        if mgr is not None:
            mgr.on_message(username)

    @property
    def total_users_tracked(self) -> int:
        """Total unique users across all managed channels (before next prune)."""
        return sum(m.user_count for m in self._managers.values())

    def _ensure(self, domain: str, channel: str) -> UserHistoryManager:
        key = f"{domain}/{channel}"
        if key not in self._managers:
            self._managers[key] = UserHistoryManager(domain, channel, self.retention_seconds)
        return self._managers[key]
