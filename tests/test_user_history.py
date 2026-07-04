"""Tests for user history tracking."""

import time

import pytest

from kryten_moderator.user_history import (
    MAX_RETENTION_SECONDS,
    Session,
    UserHistoryManager,
    UserHistoryRegistry,
    UserRecord,
)

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class TestSession:
    def test_is_active_when_no_left_at(self):
        s = Session(joined_at=100.0)
        assert s.is_active is True

    def test_not_active_when_left_at_set(self):
        s = Session(joined_at=100.0, left_at=200.0)
        assert s.is_active is False

    def test_duration_none_when_active(self):
        s = Session(joined_at=100.0)
        assert s.duration_seconds is None

    def test_duration_calculated_when_closed(self):
        s = Session(joined_at=100.0, left_at=145.0)
        assert s.duration_seconds == pytest.approx(45.0)

    def test_duration_zero_for_instant_leave(self):
        s = Session(joined_at=100.0, left_at=100.0)
        assert s.duration_seconds == pytest.approx(0.0)

    def test_to_dict_keys(self):
        s = Session(
            joined_at=1_751_000_000.0, left_at=1_751_000_060.0, ip="1.2.3.x", message_count=3
        )
        d = s.to_dict()
        assert set(d) == {"joined_at", "left_at", "duration_seconds", "ip", "message_count"}
        assert d["duration_seconds"] == pytest.approx(60.0)
        assert d["ip"] == "1.2.3.x"
        assert d["message_count"] == 3

    def test_to_dict_active_session_has_null_left_at(self):
        s = Session(joined_at=1_751_000_000.0)
        d = s.to_dict()
        assert d["left_at"] is None
        assert d["duration_seconds"] is None


# ---------------------------------------------------------------------------
# UserRecord
# ---------------------------------------------------------------------------


class TestUserRecord:
    def test_session_count(self):
        r = UserRecord("Alice", sessions=[Session(1.0), Session(2.0)])
        assert r.session_count == 2

    def test_total_messages(self):
        r = UserRecord(
            "Alice",
            sessions=[
                Session(1.0, message_count=3),
                Session(2.0, message_count=7),
            ],
        )
        assert r.total_messages == 10

    def test_total_messages_empty(self):
        r = UserRecord("Alice")
        assert r.total_messages == 0

    def test_last_seen_uses_left_at_when_present(self):
        r = UserRecord("Alice", sessions=[Session(1.0, left_at=5.0)])
        assert r.last_seen == pytest.approx(5.0)

    def test_last_seen_falls_back_to_joined_at(self):
        r = UserRecord("Alice", sessions=[Session(joined_at=1.0)])
        assert r.last_seen == pytest.approx(1.0)

    def test_last_seen_uses_last_session(self):
        r = UserRecord(
            "Alice",
            sessions=[
                Session(1.0, left_at=2.0),
                Session(10.0, left_at=20.0),
            ],
        )
        assert r.last_seen == pytest.approx(20.0)

    def test_last_seen_none_when_no_sessions(self):
        r = UserRecord("Alice")
        assert r.last_seen is None

    def test_active_session_finds_open_session(self):
        s1 = Session(1.0, left_at=2.0)
        s2 = Session(3.0)  # open
        r = UserRecord("Alice", sessions=[s1, s2])
        assert r.active_session is s2

    def test_active_session_prefers_most_recent(self):
        s1 = Session(1.0)  # open but older
        s2 = Session(3.0)  # open and newer
        r = UserRecord("Alice", sessions=[s1, s2])
        assert r.active_session is s2

    def test_active_session_none_when_all_closed(self):
        r = UserRecord("Alice", sessions=[Session(1.0, left_at=2.0)])
        assert r.active_session is None

    def test_to_dict_shape(self):
        r = UserRecord(
            "Alice",
            sessions=[
                Session(1_751_000_000.0, left_at=1_751_000_030.0, ip="1.2.3.x", message_count=2)
            ],
        )
        d = r.to_dict(moderation_action="ban")
        assert d["username"] == "Alice"
        assert d["moderation_action"] == "ban"
        assert d["session_count"] == 1
        assert d["total_messages"] == 2
        assert d["last_seen"] is not None
        assert len(d["sessions"]) == 1

    def test_to_dict_no_moderation(self):
        r = UserRecord("Alice", sessions=[Session(1.0)])
        d = r.to_dict()
        assert d["moderation_action"] is None


# ---------------------------------------------------------------------------
# UserHistoryManager
# ---------------------------------------------------------------------------


class TestUserHistoryManager:
    def _mgr(self, retention: float = MAX_RETENTION_SECONDS) -> UserHistoryManager:
        return UserHistoryManager("cytu.be", "lounge", retention_seconds=retention)

    # -- on_join --

    def test_on_join_creates_record(self):
        mgr = self._mgr()
        mgr.on_join("Alice")
        assert mgr.user_count == 1

    def test_on_join_is_case_insensitive(self):
        mgr = self._mgr()
        mgr.on_join("Alice")
        mgr.on_join("ALICE")
        assert mgr.user_count == 1
        assert mgr._records["alice"].session_count == 2

    def test_on_join_stores_ip(self):
        mgr = self._mgr()
        mgr.on_join("Alice", ip="1.2.3.x")
        assert mgr._records["alice"].sessions[0].ip == "1.2.3.x"

    def test_on_join_creates_separate_sessions_per_visit(self):
        mgr = self._mgr()
        mgr.on_join("Alice")
        mgr.on_join("Alice")
        assert mgr._records["alice"].session_count == 2

    def test_on_join_session_starts_active(self):
        mgr = self._mgr()
        mgr.on_join("Alice")
        assert mgr._records["alice"].sessions[0].is_active

    # -- on_leave --

    def test_on_leave_closes_active_session(self):
        mgr = self._mgr()
        mgr.on_join("Alice")
        before = time.time()
        mgr.on_leave("Alice")
        after = time.time()
        left_at = mgr._records["alice"].sessions[0].left_at
        assert left_at is not None
        assert before <= left_at <= after

    def test_on_leave_unknown_user_is_noop(self):
        mgr = self._mgr()
        mgr.on_leave("Nobody")  # must not raise

    def test_on_leave_when_no_active_session_is_noop(self):
        mgr = self._mgr()
        mgr.on_join("Alice")
        mgr.on_leave("Alice")  # closes session
        mgr.on_leave("Alice")  # second leave — must not raise or corrupt

    # -- on_message --

    def test_on_message_increments_active_session(self):
        mgr = self._mgr()
        mgr.on_join("Alice")
        mgr.on_message("Alice")
        mgr.on_message("Alice")
        assert mgr._records["alice"].total_messages == 2

    def test_on_message_without_prior_join_creates_synthetic_session(self):
        mgr = self._mgr()
        mgr.on_message("Alice")
        assert mgr.user_count == 1
        assert mgr._records["alice"].total_messages == 1

    def test_on_message_after_leave_credits_last_session(self):
        mgr = self._mgr()
        mgr.on_join("Alice")
        mgr.on_leave("Alice")
        # Message arrives after leave (race / replay)
        mgr.on_message("Alice")
        assert mgr._records["alice"].total_messages == 1

    # -- _prune --

    def test_prune_removes_old_closed_sessions(self):
        mgr = self._mgr(retention=3600)
        old = time.time() - 7200
        mgr._records["alice"] = UserRecord(
            "alice", sessions=[Session(joined_at=old - 60, left_at=old)]
        )
        mgr._prune(time.time())
        assert mgr.user_count == 0

    def test_prune_keeps_recent_sessions(self):
        mgr = self._mgr(retention=3600)
        recent = time.time() - 30
        mgr._records["alice"] = UserRecord(
            "alice", sessions=[Session(joined_at=recent - 10, left_at=recent)]
        )
        mgr._prune(time.time())
        assert mgr.user_count == 1

    def test_prune_keeps_active_sessions_regardless_of_age(self):
        mgr = self._mgr(retention=3600)
        # Session started 2 hours ago but still open — must not be pruned
        mgr._records["alice"] = UserRecord(
            "alice", sessions=[Session(joined_at=time.time() - 7200)]
        )
        mgr._prune(time.time())
        assert mgr.user_count == 1

    def test_prune_removes_partial_old_sessions(self):
        mgr = self._mgr(retention=3600)
        now = time.time()
        mgr._records["alice"] = UserRecord(
            "alice",
            sessions=[
                Session(joined_at=now - 7200, left_at=now - 7100),  # old
                Session(joined_at=now - 30, left_at=now - 20),  # recent
            ],
        )
        mgr._prune(now)
        assert mgr._records["alice"].session_count == 1

    # -- query --

    def test_query_filters_sessions_by_window(self):
        mgr = self._mgr()
        now = time.time()
        mgr._records["alice"] = UserRecord(
            "alice",
            sessions=[
                Session(joined_at=now - 7200, left_at=now - 7190),  # 2h ago — outside 1h window
                Session(joined_at=now - 1800, left_at=now - 1790),  # 30m ago — inside 1h window
            ],
        )
        results = mgr.query(window_seconds=3600, now=now)
        assert len(results) == 1
        assert results[0].session_count == 1

    def test_query_excludes_users_with_no_recent_sessions(self):
        mgr = self._mgr()
        now = time.time()
        mgr._records["alice"] = UserRecord(
            "alice",
            sessions=[
                Session(joined_at=now - 7200, left_at=now - 7190),
            ],
        )
        assert mgr.query(window_seconds=3600, now=now) == []

    def test_query_includes_active_sessions_regardless_of_window(self):
        mgr = self._mgr()
        now = time.time()
        # Session started 2 hours ago but still open
        mgr._records["alice"] = UserRecord("alice", sessions=[Session(joined_at=now - 7200)])
        results = mgr.query(window_seconds=3600, now=now)
        assert len(results) == 1

    def test_query_sorted_most_recent_first(self):
        mgr = self._mgr()
        now = time.time()
        mgr._records["alice"] = UserRecord(
            "alice", sessions=[Session(joined_at=now - 500, left_at=now - 490)]
        )
        mgr._records["bob"] = UserRecord(
            "bob", sessions=[Session(joined_at=now - 100, left_at=now - 90)]
        )
        results = mgr.query(window_seconds=3600, now=now)
        assert results[0].username == "bob"
        assert results[1].username == "alice"

    def test_query_caps_window_at_retention(self):
        mgr = self._mgr(retention=1800)  # 30-min retention
        now = time.time()
        # Session 20 min ago — within retention but > requested 1h
        # (window will be capped at 30 min retention so this session is included)
        mgr._records["alice"] = UserRecord(
            "alice", sessions=[Session(joined_at=now - 1200, left_at=now - 1190)]
        )
        # Requesting 2 hours but retention is only 30 min
        results = mgr.query(window_seconds=7200, now=now)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# UserHistoryRegistry
# ---------------------------------------------------------------------------


class TestUserHistoryRegistry:
    def test_initialize_all_creates_managers(self):
        reg = UserHistoryRegistry()
        reg.initialize_all([{"domain": "cytu.be", "channel": "lounge"}])
        assert reg.get_manager("cytu.be", "lounge") is not None

    def test_get_manager_returns_none_for_unknown_channel(self):
        reg = UserHistoryRegistry()
        assert reg.get_manager("cytu.be", "ghost") is None

    def test_on_join_routes_to_correct_manager(self):
        reg = UserHistoryRegistry()
        reg.initialize_all([{"domain": "cytu.be", "channel": "lounge"}])
        reg.on_join("cytu.be", "lounge", "Alice")
        assert reg.get_manager("cytu.be", "lounge").user_count == 1

    def test_on_leave_routes_to_correct_manager(self):
        reg = UserHistoryRegistry()
        reg.initialize_all([{"domain": "cytu.be", "channel": "lounge"}])
        reg.on_join("cytu.be", "lounge", "Alice")
        reg.on_leave("cytu.be", "lounge", "Alice")
        mgr = reg.get_manager("cytu.be", "lounge")
        assert mgr._records["alice"].sessions[0].left_at is not None

    def test_on_message_routes_to_correct_manager(self):
        reg = UserHistoryRegistry()
        reg.initialize_all([{"domain": "cytu.be", "channel": "lounge"}])
        reg.on_join("cytu.be", "lounge", "Alice")
        reg.on_message("cytu.be", "lounge", "Alice")
        assert reg.get_manager("cytu.be", "lounge")._records["alice"].total_messages == 1

    def test_on_leave_unknown_channel_is_noop(self):
        reg = UserHistoryRegistry()
        reg.on_leave("cytu.be", "ghost", "Alice")  # must not raise

    def test_on_message_unknown_channel_is_noop(self):
        reg = UserHistoryRegistry()
        reg.on_message("cytu.be", "ghost", "Alice")  # must not raise

    def test_on_join_auto_creates_manager_for_new_channel(self):
        reg = UserHistoryRegistry()
        reg.on_join("cytu.be", "newchannel", "Alice")
        assert reg.get_manager("cytu.be", "newchannel") is not None

    def test_total_users_tracked(self):
        reg = UserHistoryRegistry()
        reg.initialize_all([{"domain": "cytu.be", "channel": "lounge"}])
        reg.on_join("cytu.be", "lounge", "Alice")
        reg.on_join("cytu.be", "lounge", "Bob")
        assert reg.total_users_tracked == 2

    def test_multiple_channels_are_independent(self):
        reg = UserHistoryRegistry()
        reg.initialize_all(
            [
                {"domain": "cytu.be", "channel": "lounge"},
                {"domain": "cytu.be", "channel": "music"},
            ]
        )
        reg.on_join("cytu.be", "lounge", "Alice")
        reg.on_join("cytu.be", "music", "Bob")
        assert reg.get_manager("cytu.be", "lounge").user_count == 1
        assert reg.get_manager("cytu.be", "music").user_count == 1
