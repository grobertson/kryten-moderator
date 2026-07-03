"""Unit tests for the PM command handler."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kryten_moderator.pm_handler import RANK_ADMIN, RANK_MOD, PMCommandHandler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    username: str = "ModUser",
    message: str = "help",
    channel: str = "lounge",
    domain: str = "cytu.be",
):
    """Return a minimal mock ChatMessageEvent.

    Note: rank is intentionally absent — PM events always carry rank=0 from
    CyTube.  Tests that care about rank should configure client.get_user instead.
    """
    ev = MagicMock()
    ev.username = username
    ev.message = message
    ev.channel = channel
    ev.domain = domain
    return ev


def _make_app(
    bot_username: str = "Kryten",
    running: bool = True,
):
    """Return a minimal mock ModeratorService."""
    app = MagicMock()
    app.config = {"service": {"bot_username": bot_username}}
    app._running = running
    app._events_processed = 0
    app._commands_processed = 0
    app._bans_enforced = 2
    app._smutes_enforced = 3
    app._mutes_enforced = 1
    app._ip_correlations = 4
    app._pattern_matches = 5
    app.get_uptime_seconds = MagicMock(return_value=3661.0)

    # moderation_lists mock
    mod_list = AsyncMock()
    mod_list.list_all = AsyncMock(return_value=[])
    mod_list.get = AsyncMock(return_value=None)
    mod_list.add = AsyncMock()
    mod_list.remove = AsyncMock(return_value=False)

    app.moderation_lists = AsyncMock()
    app.moderation_lists.get_list = AsyncMock(return_value=mod_list)

    # pattern_managers mock
    pm = AsyncMock()
    pm.list_all = AsyncMock(return_value=[])
    pm.add = AsyncMock()
    pm.remove = AsyncMock(return_value=False)
    app.pattern_managers = AsyncMock()
    app.pattern_managers.get_manager = AsyncMock(return_value=pm)

    return app


def _make_handler(app=None, bot_username="Kryten", rank=RANK_MOD):
    client = MagicMock()
    client.send_pm = AsyncMock()
    client.kick_user = AsyncMock()
    client.mute_user = AsyncMock()
    client.shadow_mute_user = AsyncMock()
    client.unmute_user = AsyncMock()
    # Simulate Kryten-Robot returning the given rank for any user lookup
    client.get_user = AsyncMock(return_value={"rank": rank, "name": "ModUser"})

    if app is None:
        app = _make_app(bot_username=bot_username)

    handler = PMCommandHandler(app, client)
    return handler


# ---------------------------------------------------------------------------
# Rank gating
# ---------------------------------------------------------------------------


class TestRankGating:
    @pytest.mark.asyncio
    async def test_rank_below_mod_gets_refused(self):
        handler = _make_handler(rank=1)
        event = _make_event(message="ban someone")
        await handler._handle_pm(event)
        handler.client.send_pm.assert_awaited_once()
        msg = handler.client.send_pm.call_args[0][2]
        assert "rank" in msg.lower()
        assert "⛔" in msg

    @pytest.mark.asyncio
    async def test_rank_mod_can_send_commands(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="help")
        await handler._handle_pm(event)
        handler.client.send_pm.assert_awaited()

    @pytest.mark.asyncio
    async def test_admin_command_rejected_for_mod_rank(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="pattern list")
        await handler._handle_pm(event)
        handler.client.send_pm.assert_awaited()
        msg = handler.client.send_pm.call_args[0][2]
        assert "admin" in msg.lower() or str(RANK_ADMIN) in msg

    @pytest.mark.asyncio
    async def test_admin_command_allowed_for_admin_rank(self):
        handler = _make_handler(rank=RANK_ADMIN)
        event = _make_event(message="pattern list")
        await handler._handle_pm(event)
        handler.client.send_pm.assert_awaited()
        # Should NOT get a rank refusal
        msg = handler.client.send_pm.call_args[0][2]
        assert "admin rank" not in msg.lower()

    @pytest.mark.asyncio
    async def test_rank_lookup_failure_refuses_command(self):
        """If Kryten-Robot is unreachable, rank defaults to 0 and command is refused."""
        handler = _make_handler(rank=RANK_MOD)
        handler.client.get_user = AsyncMock(side_effect=Exception("NATS timeout"))
        event = _make_event(message="help")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "rank" in msg.lower()

    @pytest.mark.asyncio
    async def test_rank_lookup_returns_none_refuses_command(self):
        """If get_user returns None (user not found), command is refused."""
        handler = _make_handler(rank=RANK_MOD)
        handler.client.get_user = AsyncMock(return_value=None)
        event = _make_event(message="help")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "rank" in msg.lower()


# ---------------------------------------------------------------------------
# Self-PM filtering
# ---------------------------------------------------------------------------


class TestSelfPMFilter:
    @pytest.mark.asyncio
    async def test_ignores_own_username(self):
        handler = _make_handler(bot_username="Kryten")
        event = _make_event(username="Kryten", message="help")
        await handler._handle_pm(event)
        handler.client.send_pm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_own_username_case_insensitive(self):
        handler = _make_handler(bot_username="Kryten")
        event = _make_event(username="kryten", message="help")
        await handler._handle_pm(event)
        handler.client.send_pm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_other_user_not_filtered(self):
        handler = _make_handler(bot_username="Kryten")
        event = _make_event(username="SomeOtherUser", message="help")
        await handler._handle_pm(event)
        handler.client.send_pm.assert_awaited()


# ---------------------------------------------------------------------------
# help command
# ---------------------------------------------------------------------------


class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_help_shows_commands(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="help")
        await handler._handle_pm(event)
        calls = [c[0][2] for c in handler.client.send_pm.call_args_list]
        combined = "\n".join(calls)
        for cmd in ("ban", "unban", "smute", "unsmute", "mute", "unmute",
                    "list", "check", "about", "help"):
            assert cmd in combined

    @pytest.mark.asyncio
    async def test_help_shows_admin_section_for_admin(self):
        handler = _make_handler(rank=RANK_ADMIN)
        event = _make_event(message="help")
        await handler._handle_pm(event)
        calls = [c[0][2] for c in handler.client.send_pm.call_args_list]
        combined = "\n".join(calls)
        assert "pattern" in combined

    @pytest.mark.asyncio
    async def test_help_hides_admin_section_for_mod(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="help")
        await handler._handle_pm(event)
        calls = [c[0][2] for c in handler.client.send_pm.call_args_list]
        combined = "\n".join(calls)
        assert "pattern" not in combined


# ---------------------------------------------------------------------------
# about command
# ---------------------------------------------------------------------------


class TestAboutCommand:
    @pytest.mark.asyncio
    async def test_about_shows_version_and_uptime(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="about")
        await handler._handle_pm(event)
        calls = [c[0][2] for c in handler.client.send_pm.call_args_list]
        combined = "\n".join(calls)
        # uptime should show hours/minutes/seconds
        assert "1h 1m 1s" in combined
        # action counts
        assert "2" in combined  # bans_enforced
        assert "3" in combined  # smutes_enforced

    @pytest.mark.asyncio
    async def test_about_shows_all_stat_fields(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="about")
        await handler._handle_pm(event)
        calls = [c[0][2] for c in handler.client.send_pm.call_args_list]
        combined = "\n".join(calls)
        for keyword in ("Uptime", "Actions", "IP", "Pattern", "Events", "Commands"):
            assert keyword in combined


# ---------------------------------------------------------------------------
# ban / unban commands
# ---------------------------------------------------------------------------


class TestBanCommands:
    @pytest.mark.asyncio
    async def test_ban_requires_username(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="ban")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "usage" in msg.lower() or "Usage" in msg

    @pytest.mark.asyncio
    async def test_ban_adds_user_and_replies(self):
        app = _make_app()
        entry = MagicMock()
        entry.action = "ban"
        entry.reason = "spam"
        app.moderation_lists.get_list.return_value.add = AsyncMock(return_value=entry)
        handler = _make_handler(app=app, rank=RANK_MOD)

        event = _make_event(message="ban BadActor spam")
        await handler._handle_pm(event)

        app.moderation_lists.get_list.return_value.add.assert_awaited_once()
        call_kwargs = app.moderation_lists.get_list.return_value.add.call_args[1]
        assert call_kwargs["username"] == "BadActor"
        assert call_kwargs["action"] == "ban"
        assert call_kwargs["reason"] == "spam"
        assert call_kwargs["moderator"] == "ModUser"

        msg = handler.client.send_pm.call_args[0][2]
        assert "Banned" in msg
        assert "BadActor" in msg

    @pytest.mark.asyncio
    async def test_unban_when_user_not_in_list(self):
        app = _make_app()
        app.moderation_lists.get_list.return_value.remove = AsyncMock(return_value=False)
        handler = _make_handler(app=app, rank=RANK_MOD)

        event = _make_event(message="unban nobody")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "not in" in msg.lower()

    @pytest.mark.asyncio
    async def test_unban_removes_user(self):
        app = _make_app()
        app.moderation_lists.get_list.return_value.remove = AsyncMock(return_value=True)
        handler = _make_handler(app=app, rank=RANK_MOD)

        event = _make_event(message="unban GoodActor")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "Removed" in msg
        assert "GoodActor" in msg


# ---------------------------------------------------------------------------
# smute / mute variants
# ---------------------------------------------------------------------------


class TestSmuteMuteCommands:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("cmd,action", [
        ("smute SilentOne reason here", "smute"),
        ("mute LoudOne being loud", "mute"),
    ])
    async def test_smute_and_mute_add_entry(self, cmd, action):
        app = _make_app()
        entry = MagicMock()
        entry.action = action
        entry.reason = "test"
        app.moderation_lists.get_list.return_value.add = AsyncMock(return_value=entry)
        handler = _make_handler(app=app, rank=RANK_MOD)

        event = _make_event(message=cmd)
        await handler._handle_pm(event)

        call_kwargs = app.moderation_lists.get_list.return_value.add.call_args[1]
        assert call_kwargs["action"] == action


# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------


class TestListCommand:
    @pytest.mark.asyncio
    async def test_list_empty(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="list")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "No moderation" in msg

    @pytest.mark.asyncio
    async def test_list_invalid_filter(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="list kick")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "Invalid" in msg

    @pytest.mark.asyncio
    async def test_list_with_entries(self):
        app = _make_app()
        entry = MagicMock()
        entry.username = "Troll"
        entry.action = "ban"
        entry.reason = "harassment"
        app.moderation_lists.get_list.return_value.list_all = AsyncMock(
            return_value=[entry]
        )
        handler = _make_handler(app=app, rank=RANK_MOD)

        event = _make_event(message="list")
        await handler._handle_pm(event)
        calls = [c[0][2] for c in handler.client.send_pm.call_args_list]
        combined = "\n".join(calls)
        assert "Troll" in combined
        assert "BAN" in combined

    @pytest.mark.asyncio
    async def test_list_pagination_stores_pages(self):
        """More than PAGE_SIZE entries should trigger pagination state."""
        app = _make_app()
        # 25 entries triggers pagination (header + 25 lines = 26 lines > PAGE_SIZE 20)
        entries = []
        for i in range(25):
            e = MagicMock()
            e.username = f"user{i}"
            e.action = "ban"
            e.reason = None
            entries.append(e)
        app.moderation_lists.get_list.return_value.list_all = AsyncMock(return_value=entries)
        handler = _make_handler(app=app, rank=RANK_MOD)

        event = _make_event(message="list")
        await handler._handle_pm(event)

        key = (event.username, event.channel, event.domain)
        assert key in handler._pending_pages


# ---------------------------------------------------------------------------
# more command
# ---------------------------------------------------------------------------


class TestMoreCommand:
    @pytest.mark.asyncio
    async def test_more_no_pending(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="more")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "No further" in msg or "✅" in msg

    @pytest.mark.asyncio
    async def test_more_shows_next_page(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="more")
        key = (event.username, event.channel, event.domain)
        handler._pending_pages[key] = [["line A", "line B"]]

        await handler._handle_pm(event)

        calls = [c[0][2] for c in handler.client.send_pm.call_args_list]
        assert "line A" in calls
        assert "line B" in calls
        # After consuming the only page it should be gone
        assert key not in handler._pending_pages


# ---------------------------------------------------------------------------
# check command
# ---------------------------------------------------------------------------


class TestCheckCommand:
    @pytest.mark.asyncio
    async def test_check_not_found(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="check cleanuser")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "not in" in msg.lower()

    @pytest.mark.asyncio
    async def test_check_found(self):
        app = _make_app()
        entry = MagicMock()
        entry.username = "BadActor"
        entry.action = "ban"
        entry.reason = "harassment"
        entry.moderator = "ModUser"
        entry.timestamp = "2026-01-01T12:00:00+00:00"
        entry.ip_correlation_source = None
        entry.pattern_match = None
        app.moderation_lists.get_list.return_value.get = AsyncMock(return_value=entry)
        handler = _make_handler(app=app, rank=RANK_MOD)

        event = _make_event(message="check BadActor")
        await handler._handle_pm(event)
        calls = [c[0][2] for c in handler.client.send_pm.call_args_list]
        combined = "\n".join(calls)
        assert "BAN" in combined
        assert "ModUser" in combined

    @pytest.mark.asyncio
    async def test_check_requires_username(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="check")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "Usage" in msg


# ---------------------------------------------------------------------------
# pattern commands (admin only)
# ---------------------------------------------------------------------------


class TestPatternCommands:
    @pytest.mark.asyncio
    async def test_pattern_list_empty(self):
        handler = _make_handler(rank=RANK_ADMIN)
        event = _make_event(message="pattern list")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "No patterns" in msg

    @pytest.mark.asyncio
    async def test_pattern_add_basic(self):
        app = _make_app()
        entry = MagicMock()
        entry.pattern = "1488"
        entry.is_regex = False
        entry.action = "ban"
        app.pattern_managers.get_manager.return_value.add = AsyncMock(return_value=entry)
        handler = _make_handler(app=app, rank=RANK_ADMIN)

        event = _make_event(message="pattern add 1488")
        await handler._handle_pm(event)

        app.pattern_managers.get_manager.return_value.add.assert_awaited_once()
        call_kwargs = app.pattern_managers.get_manager.return_value.add.call_args[1]
        assert call_kwargs["pattern"] == "1488"
        assert call_kwargs["is_regex"] is False
        assert call_kwargs["action"] == "ban"

        msg = handler.client.send_pm.call_args[0][2]
        assert "Pattern added" in msg

    @pytest.mark.asyncio
    async def test_pattern_add_with_regex_flag(self):
        app = _make_app()
        entry = MagicMock()
        entry.pattern = "^h.tl.r$"
        entry.is_regex = True
        entry.action = "ban"
        app.pattern_managers.get_manager.return_value.add = AsyncMock(return_value=entry)
        handler = _make_handler(app=app, rank=RANK_ADMIN)

        event = _make_event(message="pattern add ^h.tl.r$ regex ban Nazi ref")
        await handler._handle_pm(event)

        call_kwargs = app.pattern_managers.get_manager.return_value.add.call_args[1]
        assert call_kwargs["is_regex"] is True
        assert call_kwargs["action"] == "ban"
        assert call_kwargs["description"] == "Nazi ref"

    @pytest.mark.asyncio
    async def test_pattern_remove_not_found(self):
        app = _make_app()
        app.pattern_managers.get_manager.return_value.remove = AsyncMock(return_value=False)
        handler = _make_handler(app=app, rank=RANK_ADMIN)

        event = _make_event(message="pattern remove nonexistent")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "not found" in msg.lower()

    @pytest.mark.asyncio
    async def test_pattern_remove_success(self):
        app = _make_app()
        app.pattern_managers.get_manager.return_value.remove = AsyncMock(return_value=True)
        handler = _make_handler(app=app, rank=RANK_ADMIN)

        event = _make_event(message="pattern remove 1488")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "removed" in msg.lower()

    @pytest.mark.asyncio
    async def test_pattern_unknown_sub(self):
        handler = _make_handler(rank=RANK_ADMIN)
        event = _make_event(message="pattern purge")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "Unknown sub-command" in msg


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------


class TestUnknownCommand:
    @pytest.mark.asyncio
    async def test_unknown_command_suggests_help(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="frobnicate")
        await handler._handle_pm(event)
        msg = handler.client.send_pm.call_args[0][2]
        assert "Unknown command" in msg or "❓" in msg
        assert "help" in msg.lower()


# ---------------------------------------------------------------------------
# Empty message
# ---------------------------------------------------------------------------


class TestEmptyMessage:
    @pytest.mark.asyncio
    async def test_empty_message_no_reply(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="   ")
        await handler._handle_pm(event)
        handler.client.send_pm.assert_not_awaited()


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


class TestCounters:
    @pytest.mark.asyncio
    async def test_events_processed_incremented(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="help")
        assert handler.app._events_processed == 0
        await handler._handle_pm(event)
        assert handler.app._events_processed == 1

    @pytest.mark.asyncio
    async def test_commands_processed_incremented(self):
        handler = _make_handler(rank=RANK_MOD)
        event = _make_event(message="help")
        assert handler.app._commands_processed == 0
        await handler._handle_pm(event)
        assert handler.app._commands_processed == 1

    @pytest.mark.asyncio
    async def test_rank_refusal_does_not_increment_command_counter(self):
        handler = _make_handler(rank=0)
        event = _make_event(message="ban someone")
        await handler._handle_pm(event)
        # events_processed is incremented (we received the event)
        assert handler.app._events_processed == 1
        # commands_processed is NOT incremented (command was rejected)
        assert handler.app._commands_processed == 0
