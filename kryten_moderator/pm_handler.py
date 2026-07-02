"""PM command handler for moderator service.

Handles private message commands from authorized channel moderators.
All PM messages received by the bot are treated as commands — no prefix
required.  The bot's own messages are silently ignored via the
``service.bot_username`` config key.

Rank requirements
-----------------
Rank 3+  — Moderation commands: ban, unban, smute, unsmute, mute, unmute,
            list, more, check, about, help
Rank 4+  — Admin commands: pattern list / add / remove

Formatting
----------
CyTube PM messages support a subset of IRC-style control characters.
This module uses:
    \\x02  bold on / off toggle
    \\x0f  reset all formatting
"""

import logging
from typing import Any

from kryten import ChatMessageEvent, KrytenClient

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

BOLD = "\x02"
RESET = "\x0f"

# Minimum rank thresholds
RANK_MOD = 3
RANK_ADMIN = 4

# Number of lines shown per page before prompting "more"
PAGE_SIZE = 20


def b(text: str) -> str:
    """Return *text* wrapped in bold control characters."""
    return f"{BOLD}{text}{BOLD}"


# ---------------------------------------------------------------------------
# PMCommandHandler
# ---------------------------------------------------------------------------


class PMCommandHandler:
    """Dispatch private-message commands from authorised channel moderators.

    Commands (rank 3+)
    ------------------
    ban <user> [reason]       Add user to ban list (kicked on join)
    unban <user>              Remove ban
    smute <user> [reason]     Shadow-mute user (they are unaware)
    unsmute <user>            Remove shadow mute
    mute <user> [reason]      Visible mute user
    unmute <user>             Remove mute
    list [ban|smute|mute]     List moderated users (paginated)
    more                      Show next page of results
    check <user>              Show moderation status for a user
    about                     Service version, uptime and action counts
    help                      Command reference

    Commands (rank 4+)
    ------------------
    pattern list              List banned username patterns
    pattern add <pat> [regex] [ban|smute|mute] [desc]
                              Add a banned username pattern
    pattern remove <pat>      Remove a banned username pattern
    """

    def __init__(self, app_reference: Any, client: KrytenClient) -> None:
        self.app = app_reference
        self.client = client
        self.logger = logging.getLogger(__name__)

        # Pagination state keyed by (username, channel, domain)
        self._pending_pages: dict[tuple[str, str, str], list[list[str]]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Register the PM event handler with the KrytenClient."""

        @self.client.on("pm")
        async def handle_pm(event: ChatMessageEvent) -> None:
            await self._handle_pm(event)

        self.logger.info("PM command handler registered")

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    async def _handle_pm(self, event: ChatMessageEvent) -> None:
        """Entry point for every incoming PM event."""
        self.app._events_processed += 1

        # Ignore messages the bot sends to itself
        bot_username = self.app.config.get("service", {}).get("bot_username")
        if bot_username and event.username.lower() == bot_username.lower():
            return

        # Enforce minimum rank
        if event.rank < RANK_MOD:
            await self._reply(
                event,
                f"PM commands require moderator rank ({RANK_MOD}+). "
                f"Your rank: {event.rank}.",
            )
            return

        text = event.message.strip()
        if not text:
            return

        parts = text.split(None, 1)
        command = parts[0].lower()
        args_str = parts[1] if len(parts) > 1 else ""

        self.logger.info(
            "PM command from %s (rank %d) in %s/%s: %r",
            event.username,
            event.rank,
            event.domain,
            event.channel,
            command,
        )
        self.app._commands_processed += 1

        try:
            await self._dispatch(event, command, args_str)
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Error handling PM command %r from %s: %s",
                command,
                event.username,
                exc,
                exc_info=True,
            )
            await self._reply(event, f"Error: {exc}")

    async def _dispatch(
        self, event: ChatMessageEvent, command: str, args_str: str
    ) -> None:
        """Route *command* to the correct handler."""
        mod_commands: dict[str, Any] = {
            "ban": self._cmd_ban,
            "unban": self._cmd_unban,
            "smute": self._cmd_smute,
            "unsmute": self._cmd_unsmute,
            "mute": self._cmd_mute,
            "unmute": self._cmd_unmute,
            "list": self._cmd_list,
            "more": self._cmd_more,
            "check": self._cmd_check,
            "about": self._cmd_about,
            "help": self._cmd_help,
        }
        admin_commands: dict[str, Any] = {
            "pattern": self._cmd_pattern,
        }

        if command in mod_commands:
            await mod_commands[command](event, args_str)
        elif command in admin_commands:
            if event.rank < RANK_ADMIN:
                await self._reply(
                    event,
                    f"Command {b(command)!r} requires admin rank ({RANK_ADMIN}+). "
                    f"Your rank: {event.rank}.",
                )
            else:
                await admin_commands[command](event, args_str)
        else:
            await self._reply(
                event,
                f"Unknown command: {b(command)}. "
                f"Send {b('help')} for the list of available commands.",
            )

    # ------------------------------------------------------------------
    # Mod commands (rank 3+)
    # ------------------------------------------------------------------

    async def _cmd_ban(self, event: ChatMessageEvent, args_str: str) -> None:
        """ban <username> [reason]"""
        parts = args_str.split(None, 1)
        if not parts:
            await self._reply(event, f"Usage: {b('ban')} <username> [reason]")
            return
        await self._add_moderation(
            event,
            username=parts[0],
            action="ban",
            reason=parts[1] if len(parts) > 1 else None,
        )

    async def _cmd_unban(self, event: ChatMessageEvent, args_str: str) -> None:
        """unban <username>"""
        parts = args_str.split()
        if not parts:
            await self._reply(event, f"Usage: {b('unban')} <username>")
            return
        await self._remove_moderation(event, parts[0])

    async def _cmd_smute(self, event: ChatMessageEvent, args_str: str) -> None:
        """smute <username> [reason]"""
        parts = args_str.split(None, 1)
        if not parts:
            await self._reply(event, f"Usage: {b('smute')} <username> [reason]")
            return
        await self._add_moderation(
            event,
            username=parts[0],
            action="smute",
            reason=parts[1] if len(parts) > 1 else None,
        )

    async def _cmd_unsmute(self, event: ChatMessageEvent, args_str: str) -> None:
        """unsmute <username>"""
        parts = args_str.split()
        if not parts:
            await self._reply(event, f"Usage: {b('unsmute')} <username>")
            return
        await self._remove_moderation(event, parts[0])

    async def _cmd_mute(self, event: ChatMessageEvent, args_str: str) -> None:
        """mute <username> [reason]"""
        parts = args_str.split(None, 1)
        if not parts:
            await self._reply(event, f"Usage: {b('mute')} <username> [reason]")
            return
        await self._add_moderation(
            event,
            username=parts[0],
            action="mute",
            reason=parts[1] if len(parts) > 1 else None,
        )

    async def _cmd_unmute(self, event: ChatMessageEvent, args_str: str) -> None:
        """unmute <username>"""
        parts = args_str.split()
        if not parts:
            await self._reply(event, f"Usage: {b('unmute')} <username>")
            return
        await self._remove_moderation(event, parts[0])

    async def _cmd_list(self, event: ChatMessageEvent, args_str: str) -> None:
        """list [ban|smute|mute] — paginated list of moderated users."""
        filter_action = args_str.strip().lower() or None
        if filter_action and filter_action not in ("ban", "smute", "mute"):
            await self._reply(
                event,
                f"Invalid filter. Use: {b('list')}, {b('list ban')}, "
                f"{b('list smute')}, or {b('list mute')}.",
            )
            return

        if not self.app.moderation_lists:
            await self._reply(event, "Moderation system is not ready.")
            return

        mod_list = await self.app.moderation_lists.get_list(event.domain, event.channel)
        entries = await mod_list.list_all(filter_action=filter_action)

        if not entries:
            label = f" ({filter_action})" if filter_action else ""
            await self._reply(event, f"No moderation entries{label} for {b(event.channel)}.")
            return

        filter_tag = f"  [{filter_action}]" if filter_action else ""
        lines: list[str] = [
            f"{b('Moderation list')} — {b(event.channel)}{filter_tag}"
            f"  ({len(entries)} entries)",
        ]
        for entry in entries:
            reason_str = f"  {RESET}{entry.reason}" if entry.reason else ""
            lines.append(f"  {b(entry.action.upper())}  {entry.username}{reason_str}")

        await self._send_paginated(event, lines)

    async def _cmd_more(self, event: ChatMessageEvent, args_str: str) -> None:  # noqa: ARG002
        """Show the next page of a previous paginated result."""
        key = (event.username, event.channel, event.domain)
        pages = self._pending_pages.get(key)
        if not pages:
            await self._reply(event, "No further results to show.")
            return

        page = pages.pop(0)
        if not pages:
            del self._pending_pages[key]

        for line in page:
            await self._reply(event, line)

        if key in self._pending_pages:
            remaining = sum(len(p) for p in self._pending_pages[key])
            await self._reply(
                event,
                f"— {remaining} more line(s). "
                f"Send {b('more')} to continue.",
            )

    async def _cmd_check(self, event: ChatMessageEvent, args_str: str) -> None:
        """check <username> — show moderation status for a user."""
        parts = args_str.split()
        if not parts:
            await self._reply(event, f"Usage: {b('check')} <username>")
            return

        username = parts[0]
        if not self.app.moderation_lists:
            await self._reply(event, "Moderation system is not ready.")
            return

        mod_list = await self.app.moderation_lists.get_list(event.domain, event.channel)
        entry = await mod_list.get(username)

        if not entry:
            await self._reply(event, f"{b(username)} is not in the moderation list.")
            return

        # Normalise timestamp for display: drop microseconds and replace T
        ts = entry.timestamp[:19].replace("T", " ") if entry.timestamp else "unknown"

        lines = [
            f"{b(username)} — {b(event.channel)}",
            f"  Action :  {b(entry.action.upper())}",
            f"  Added by: {entry.moderator}",
            f"  Date :    {ts}",
        ]
        if entry.reason:
            lines.append(f"  Reason :  {entry.reason}")
        if entry.ip_correlation_source:
            lines.append(f"  IP match: correlated with {b(entry.ip_correlation_source)}")
        if entry.pattern_match:
            lines.append(f"  Pattern : {entry.pattern_match}")

        for line in lines:
            await self._reply(event, line)

    async def _cmd_about(self, event: ChatMessageEvent, args_str: str) -> None:  # noqa: ARG002
        """Show service version, uptime, and cumulative action counts."""
        from . import __version__

        uptime = self.app.get_uptime_seconds()
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        seconds = int(uptime % 60)

        total = (
            self.app._bans_enforced
            + self.app._smutes_enforced
            + self.app._mutes_enforced
        )

        lines = [
            f"{b('Kryten Moderator')}  v{__version__}",
            f"  Uptime :       {hours}h {minutes}m {seconds}s",
            f"  Actions taken: {total}"
            f"  (ban {self.app._bans_enforced} /"
            f" smute {self.app._smutes_enforced} /"
            f" mute {self.app._mutes_enforced})",
            f"  IP detections: {self.app._ip_correlations}",
            f"  Pattern hits:  {self.app._pattern_matches}",
            f"  Events seen:   {self.app._events_processed}",
            f"  Commands run:  {self.app._commands_processed}",
        ]
        for line in lines:
            await self._reply(event, line)

    async def _cmd_help(self, event: ChatMessageEvent, args_str: str) -> None:  # noqa: ARG002
        """Show the command reference."""
        lines = [
            f"{b('Kryten Moderator')}  — command reference",
            f"  {b('ban')} <user> [reason]        Ban user (kicked on join)",
            f"  {b('unban')} <user>               Remove ban",
            f"  {b('smute')} <user> [reason]      Shadow-mute (user unaware)",
            f"  {b('unsmute')} <user>             Remove shadow mute",
            f"  {b('mute')} <user> [reason]       Visible mute",
            f"  {b('unmute')} <user>              Remove mute",
            f"  {b('list')} [ban|smute|mute]      List moderated users",
            f"  {b('more')}                       Next page of results",
            f"  {b('check')} <user>               Moderation status",
            f"  {b('about')}                      Version and statistics",
            f"  {b('help')}                       This message",
        ]
        if event.rank >= RANK_ADMIN:
            lines += [
                f"  {b('pattern list')}               List banned patterns",
                f"  {b('pattern add')} <p> [regex] [action] [desc]",
                f"                               Add banned pattern",
                f"  {b('pattern remove')} <p>          Remove pattern",
            ]
        for line in lines:
            await self._reply(event, line)

    # ------------------------------------------------------------------
    # Admin commands (rank 4+)
    # ------------------------------------------------------------------

    async def _cmd_pattern(self, event: ChatMessageEvent, args_str: str) -> None:
        """pattern <list|add|remove> [args]"""
        parts = args_str.split(None, 1)
        if not parts:
            await self._reply(
                event,
                f"Usage: {b('pattern')} <list|add|remove> [args]",
            )
            return

        sub = parts[0].lower()
        sub_args = parts[1] if len(parts) > 1 else ""

        if sub == "list":
            await self._cmd_pattern_list(event, sub_args)
        elif sub == "add":
            await self._cmd_pattern_add(event, sub_args)
        elif sub == "remove":
            await self._cmd_pattern_remove(event, sub_args)
        else:
            await self._reply(
                event,
                f"Unknown sub-command: {b(sub)}. "
                f"Use {b('pattern list')}, {b('pattern add')}, "
                f"or {b('pattern remove')}.",
            )

    async def _cmd_pattern_list(
        self, event: ChatMessageEvent, args_str: str  # noqa: ARG002
    ) -> None:
        """List all banned username patterns for the channel."""
        if not self.app.pattern_managers:
            await self._reply(event, "Pattern matching is not enabled.")
            return

        pm = await self.app.pattern_managers.get_manager(event.domain, event.channel)
        entries = await pm.list_all()

        if not entries:
            await self._reply(event, f"No patterns configured for {b(event.channel)}.")
            return

        lines: list[str] = [
            f"{b('Banned patterns')} — {b(event.channel)}  ({len(entries)} entries)",
        ]
        for entry in entries:
            type_tag = "regex" if entry.is_regex else "text"
            desc = f"  {RESET}{entry.description}" if entry.description else ""
            lines.append(
                f"  {b(entry.action.upper())}  [{type_tag}]  {entry.pattern}{desc}"
            )

        await self._send_paginated(event, lines)

    async def _cmd_pattern_add(self, event: ChatMessageEvent, args_str: str) -> None:
        """pattern add <pattern> [regex] [ban|smute|mute] [description]"""
        if not args_str.strip():
            await self._reply(
                event,
                f"Usage: {b('pattern add')} <pattern> [regex] [ban|smute|mute] [desc]",
            )
            return

        if not self.app.pattern_managers:
            await self._reply(event, "Pattern matching is not enabled.")
            return

        tokens = args_str.split()
        pattern = tokens[0]
        is_regex = False
        action = "ban"
        remaining = tokens[1:]

        if remaining and remaining[0].lower() == "regex":
            is_regex = True
            remaining = remaining[1:]
        if remaining and remaining[0].lower() in ("ban", "smute", "mute"):
            action = remaining[0].lower()
            remaining = remaining[1:]
        description = " ".join(remaining) if remaining else None

        pm = await self.app.pattern_managers.get_manager(event.domain, event.channel)
        entry = await pm.add(
            pattern=pattern,
            is_regex=is_regex,
            action=action,
            added_by=event.username,
            description=description,
        )

        type_tag = "regex" if entry.is_regex else "text"
        await self._reply(
            event,
            f"Pattern added: {b(entry.pattern)}  [{type_tag}] → {b(entry.action.upper())}",
        )

    async def _cmd_pattern_remove(self, event: ChatMessageEvent, args_str: str) -> None:
        """pattern remove <pattern>"""
        pattern = args_str.strip()
        if not pattern:
            await self._reply(event, f"Usage: {b('pattern remove')} <pattern>")
            return

        if not self.app.pattern_managers:
            await self._reply(event, "Pattern matching is not enabled.")
            return

        pm = await self.app.pattern_managers.get_manager(event.domain, event.channel)
        removed = await pm.remove(pattern)

        if removed:
            await self._reply(event, f"Pattern removed: {b(pattern)}")
        else:
            await self._reply(event, f"Pattern not found: {b(pattern)}")

    # ------------------------------------------------------------------
    # Shared moderation helpers
    # ------------------------------------------------------------------

    async def _add_moderation(
        self,
        event: ChatMessageEvent,
        username: str,
        action: str,
        reason: str | None,
    ) -> None:
        """Add *username* to the moderation list and apply the action immediately
        if the user is currently online."""
        if not self.app.moderation_lists:
            await self._reply(event, "Moderation system is not ready.")
            return

        mod_list = await self.app.moderation_lists.get_list(event.domain, event.channel)
        entry = await mod_list.add(
            username=username,
            action=action,
            moderator=event.username,
            reason=reason,
        )

        # Attempt to apply the action right now if the user is online
        await self._apply_action_if_online(event.domain, event.channel, username, entry)

        label = {"ban": "Banned", "smute": "Shadow-muted", "mute": "Muted"}[action]
        reason_str = f"  ({reason})" if reason else ""
        await self._reply(event, f"{label}: {b(username)}{reason_str}")

    async def _remove_moderation(
        self,
        event: ChatMessageEvent,
        username: str,
    ) -> None:
        """Remove *username* from the moderation list and unmute if online."""
        if not self.app.moderation_lists:
            await self._reply(event, "Moderation system is not ready.")
            return

        mod_list = await self.app.moderation_lists.get_list(event.domain, event.channel)
        removed = await mod_list.remove(username)

        if not removed:
            await self._reply(event, f"{b(username)} is not in the moderation list.")
            return

        await self._unmute_if_online(event.domain, event.channel, username)
        await self._reply(event, f"Removed {b(username)} from the moderation list.")

    async def _apply_action_if_online(
        self,
        domain: str,
        channel: str,
        username: str,
        entry: Any,
    ) -> None:
        """Try to enforce *entry* immediately; silently ignore if user is offline."""
        try:
            if entry.action == "ban":
                await self.client.kick_user(
                    channel, username, reason=entry.reason, domain=domain
                )
                self.logger.info("PM handler: kicked %s from %s", username, channel)
            elif entry.action == "smute":
                await self.client.shadow_mute_user(channel, username, domain=domain)
                self.logger.info("PM handler: shadow-muted %s in %s", username, channel)
            elif entry.action == "mute":
                await self.client.mute_user(channel, username, domain=domain)
                self.logger.info("PM handler: muted %s in %s", username, channel)
        except Exception:  # noqa: BLE001
            # User is likely not online — action will be applied on next join
            self.logger.debug(
                "Could not apply immediate action to %s in %s (offline?)",
                username,
                channel,
            )

    async def _unmute_if_online(self, domain: str, channel: str, username: str) -> None:
        """Unmute *username* if they are currently online; silently ignore otherwise."""
        try:
            await self.client.unmute_user(channel, username, domain=domain)
            self.logger.info("PM handler: unmuted %s in %s", username, channel)
        except Exception:  # noqa: BLE001
            self.logger.debug(
                "Could not unmute %s in %s (offline?)", username, channel
            )

    # ------------------------------------------------------------------
    # Reply + pagination
    # ------------------------------------------------------------------

    async def _reply(self, event: ChatMessageEvent, message: str) -> None:
        """Send a PM reply to the originating user."""
        try:
            await self.client.send_pm(
                event.channel,
                event.username,
                message,
                domain=event.domain,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.error(
                "Failed to send PM reply to %s: %s", event.username, exc, exc_info=True
            )

    async def _send_paginated(
        self, event: ChatMessageEvent, lines: list[str]
    ) -> None:
        """Send *lines* in PAGE_SIZE chunks, storing overflow pages for ``more``."""
        key = (event.username, event.channel, event.domain)

        # Discard any previous pending pages for this user in this channel
        self._pending_pages.pop(key, None)

        pages = [lines[i : i + PAGE_SIZE] for i in range(0, len(lines), PAGE_SIZE)]
        if not pages:
            return

        for line in pages[0]:
            await self._reply(event, line)

        if len(pages) > 1:
            self._pending_pages[key] = pages[1:]
            remaining = sum(len(p) for p in pages[1:])
            await self._reply(
                event,
                f"— {remaining} more line(s). "
                f"Send {b('more')} for additional results.",
            )
