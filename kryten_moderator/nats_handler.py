"""NATS command handler for moderator service.

This module handles command queries on the kryten.moderator.command subject.
It uses KrytenClient instead of direct NATS access, following the architectural
rule that all NATS operations must go through kryten-py.
"""

import logging
from typing import Any

from kryten import KrytenClient


class ModeratorCommandHandler:
    """Handles command queries on NATS subjects owned by moderator service."""

    def __init__(self, app_reference, client: KrytenClient):
        """Initialize command handler using existing KrytenClient.

        Args:
            app_reference: Reference to ModeratorService for accessing state
            client: KrytenClient instance (already connected)
        """
        self.app = app_reference
        self.client = client
        self.logger = logging.getLogger(__name__)

        self._subscriptions: list[Any] = []

    async def connect(self) -> None:
        """Subscribe to unified command subject using KrytenClient.

        Single subject: kryten.moderator.command
        Commands are routed via 'command' field in message payload.
        """
        try:
            subject = "kryten.moderator.command"
            await self._subscribe(subject, self._handle_command)

            self.logger.info(f"Subscribed to {subject}")

        except Exception as e:
            self.logger.error(f"Failed to subscribe to command subjects: {e}", exc_info=True)
            raise

    async def disconnect(self) -> None:
        """Disconnect is handled by KrytenClient.

        No need to manually unsubscribe - KrytenClient manages all subscriptions.
        """
        self.logger.info("Command handler cleanup (managed by KrytenClient)")
        self._subscriptions.clear()

    async def _subscribe(self, subject: str, handler) -> None:
        """Subscribe to a command subject using KrytenClient's request-reply mechanism."""
        sub = await self.client.subscribe_request_reply(subject, handler)
        self._subscriptions.append(sub)
        self.logger.debug(f"Subscribed to {subject}")

    async def _handle_command(self, request: dict) -> dict:
        """Dispatch commands based on 'command' field in request.

        Request format:
            {
                "command": "system.health" | "system.stats" | etc,
                "service": "moderator",  # For routing/filtering (optional)
                ... command-specific parameters ...
            }

        Response format:
            {
                "service": "moderator",
                "command": "system.health",
                "success": true,
                "data": { ... } | "error": "message"
            }
        """
        # Increment commands counter
        self.app._commands_processed += 1

        command = request.get("command")

        if not command:
            return {"service": "moderator", "success": False, "error": "Missing 'command' field"}

        # Check service field for routing (other services can ignore)
        service = request.get("service")
        if service and service != "moderator":
            return {
                "service": "moderator",
                "success": False,
                "error": f"Command intended for '{service}', not 'moderator'",
            }

        # Dispatch to handler
        handler_map = {
            "system.ping": self._handle_system_ping,
            "system.health": self._handle_system_health,
            "system.stats": self._handle_system_stats,
            # Moderation entry commands
            "entry.add": self._handle_entry_add,
            "entry.remove": self._handle_entry_remove,
            "entry.get": self._handle_entry_get,
            "entry.list": self._handle_entry_list,
            # Pattern commands
            "pattern.add": self._handle_pattern_add,
            "pattern.remove": self._handle_pattern_remove,
            "pattern.list": self._handle_pattern_list,
        }

        handler = handler_map.get(command)
        if not handler:
            return {
                "service": "moderator",
                "command": command,
                "success": False,
                "error": f"Unknown command: {command}",
            }

        try:
            result = await handler(request)
            return {"service": "moderator", "command": command, "success": True, "data": result}
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"Error executing command '{command}': {e}", exc_info=True)
            return {"service": "moderator", "command": command, "success": False, "error": str(e)}

    async def _handle_system_ping(self, request: dict) -> dict:
        """Handle system.ping query - Simple liveness check with metadata."""
        import time
        from datetime import datetime

        from . import __version__

        # Get metrics port from config
        metrics_port = self.app.config.get("metrics", {}).get("port", 28284)

        return {
            "pong": True,
            "service": "moderator",
            "version": __version__,
            "uptime_seconds": self.app.get_uptime_seconds(),
            "timestamp": datetime.now().isoformat(),
            "metrics_endpoint": f"http://localhost:{metrics_port}/metrics",
        }

    async def _handle_system_health(self, request: dict) -> dict:
        """Handle system.health query - Get service health status."""
        from . import __version__

        return {
            "service": "moderator",
            "status": "healthy" if self.app._running else "starting",
            "version": __version__,
            "uptime_seconds": self.app.get_uptime_seconds(),
        }

    async def _handle_system_stats(self, request: dict) -> dict:
        """Handle system.stats query - Get service statistics."""
        from . import __version__

        # Get moderation list stats
        mod_entries = 0
        mod_lists = 0
        if self.app.moderation_lists:
            mod_entries = self.app.moderation_lists.total_entries
            mod_lists = self.app.moderation_lists.list_count

        # Get IP manager stats
        ip_mappings = 0
        if self.app.ip_managers:
            ip_mappings = self.app.ip_managers.total_mappings

        # Get pattern manager stats
        patterns = 0
        if self.app.pattern_managers:
            patterns = self.app.pattern_managers.total_patterns

        return {
            "service": "moderator",
            "version": __version__,
            "uptime_seconds": self.app.get_uptime_seconds(),
            "events_processed": self.app._events_processed,
            "commands_processed": self.app._commands_processed,
            "messages_checked": self.app._messages_checked,
            "messages_flagged": self.app._messages_flagged,
            "users_tracked": len(self.app._users_tracked),
            "moderation_entries": mod_entries,
            "moderation_lists": mod_lists,
            "ip_mappings": ip_mappings,
            "ip_correlations": self.app._ip_correlations,
            "patterns": patterns,
            "pattern_matches": self.app._pattern_matches,
            "bans_enforced": self.app._bans_enforced,
            "smutes_enforced": self.app._smutes_enforced,
            "mutes_enforced": self.app._mutes_enforced,
        }

    async def _handle_entry_add(self, request: dict) -> dict:
        """Handle entry.add command - Add user to moderation list.

        Request:
            {
                "command": "entry.add",
                "domain": "cytu.be",      # Optional, defaults to first channel
                "channel": "lounge",      # Required
                "username": "baduser",    # Required
                "action": "smute",        # Required: "ban", "smute", "mute"
                "reason": "trolling",     # Optional
                "moderator": "admin"      # Optional, defaults to "cli"
            }
        """
        channel = request.get("channel")
        username = request.get("username")
        action = request.get("action")
        reason = request.get("reason")
        moderator = request.get("moderator", "cli")

        # Get domain from request or use first channel's domain
        domain = request.get("domain")
        if not domain:
            channels = self.app.config.get("channels", [])
            if channels:
                domain = channels[0].get("domain", "cytu.be")
            else:
                domain = "cytu.be"

        # Validation
        if not channel:
            raise ValueError("channel is required")
        if not username:
            raise ValueError("username is required")
        if action not in ("ban", "smute", "mute"):
            raise ValueError("action must be 'ban', 'smute', or 'mute'")

        # Get moderation list for this channel
        mod_list = await self.app.moderation_lists.get_list(domain, channel)

        # Add entry
        entry = await mod_list.add(
            username=username,
            action=action,
            moderator=moderator,
            reason=reason,
        )

        # Try to apply action immediately if user is online
        await self._apply_action_if_online(domain, channel, username, entry)

        return {
            "username": entry.username,
            "action": entry.action,
            "reason": entry.reason,
            "moderator": entry.moderator,
            "timestamp": entry.timestamp,
            "channel": channel,
            "domain": domain,
        }

    async def _handle_entry_remove(self, request: dict) -> dict:
        """Handle entry.remove command - Remove user from moderation list.

        Request:
            {
                "command": "entry.remove",
                "domain": "cytu.be",      # Optional
                "channel": "lounge",      # Required
                "username": "baduser"     # Required
            }
        """
        channel = request.get("channel")
        username = request.get("username")

        domain = request.get("domain")
        if not domain:
            channels = self.app.config.get("channels", [])
            if channels:
                domain = channels[0].get("domain", "cytu.be")
            else:
                domain = "cytu.be"

        if not channel:
            raise ValueError("channel is required")
        if not username:
            raise ValueError("username is required")

        mod_list = await self.app.moderation_lists.get_list(domain, channel)
        removed = await mod_list.remove(username)

        if not removed:
            raise ValueError(f"User '{username}' not in moderation list for {channel}")

        # Unmute user if they're online
        await self._unmute_if_online(domain, channel, username)

        return {
            "username": username,
            "channel": channel,
            "domain": domain,
            "removed": True,
        }

    async def _handle_entry_get(self, request: dict) -> dict:
        """Handle entry.get command - Get moderation entry for user.

        Request:
            {
                "command": "entry.get",
                "domain": "cytu.be",      # Optional
                "channel": "lounge",      # Required
                "username": "someuser"    # Required
            }
        """
        channel = request.get("channel")
        username = request.get("username")

        domain = request.get("domain")
        if not domain:
            channels = self.app.config.get("channels", [])
            if channels:
                domain = channels[0].get("domain", "cytu.be")
            else:
                domain = "cytu.be"

        if not channel:
            raise ValueError("channel is required")
        if not username:
            raise ValueError("username is required")

        mod_list = await self.app.moderation_lists.get_list(domain, channel)
        entry = await mod_list.get(username)

        if not entry:
            return {
                "username": username,
                "channel": channel,
                "domain": domain,
                "moderated": False,
            }

        return {
            "username": entry.username,
            "channel": channel,
            "domain": domain,
            "moderated": True,
            "action": entry.action,
            "reason": entry.reason,
            "moderator": entry.moderator,
            "timestamp": entry.timestamp,
            "ips": entry.ips,
            "ip_correlation_source": entry.ip_correlation_source,
            "pattern_match": entry.pattern_match,
        }

    async def _handle_entry_list(self, request: dict) -> dict:
        """Handle entry.list command - List all moderation entries.

        Request:
            {
                "command": "entry.list",
                "domain": "cytu.be",      # Optional
                "channel": "lounge",      # Required
                "filter": "smute"         # Optional: "ban", "smute", "mute"
            }
        """
        channel = request.get("channel")
        filter_action = request.get("filter")

        domain = request.get("domain")
        if not domain:
            channels = self.app.config.get("channels", [])
            if channels:
                domain = channels[0].get("domain", "cytu.be")
            else:
                domain = "cytu.be"

        if not channel:
            raise ValueError("channel is required")

        mod_list = await self.app.moderation_lists.get_list(domain, channel)
        entries = await mod_list.list_all(filter_action=filter_action)

        return {
            "channel": channel,
            "domain": domain,
            "count": len(entries),
            "entries": [
                {
                    "username": e.username,
                    "action": e.action,
                    "reason": e.reason,
                    "moderator": e.moderator,
                    "timestamp": e.timestamp,
                    "ip_correlation_source": e.ip_correlation_source,
                    "pattern_match": e.pattern_match,
                }
                for e in entries
            ],
        }

    async def _apply_action_if_online(
        self, domain: str, channel: str, username: str, entry
    ) -> None:
        """Apply moderation action if user is currently online.

        This attempts to apply the action immediately - if the user is not
        online, the action will fail silently (they'll be moderated on next join).
        """
        try:
            if entry.action == "ban":
                await self.app.client.kick_user(channel, username, reason=entry.reason, domain=domain)
                self.logger.info(f"Applied immediate kick to {username} in {channel}")
            elif entry.action == "smute":
                await self.app.client.shadow_mute_user(channel, username, domain=domain)
                self.logger.info(f"Applied immediate smute to {username} in {channel}")
            elif entry.action == "mute":
                await self.app.client.mute_user(channel, username, domain=domain)
                self.logger.info(f"Applied immediate mute to {username} in {channel}")
        except Exception as e:
            # User may not be online, that's fine
            self.logger.debug(f"Could not apply immediate action to {username}: {e}")

    async def _unmute_if_online(self, domain: str, channel: str, username: str) -> None:
        """Unmute user if they are currently online."""
        try:
            await self.app.client.unmute_user(channel, username, domain=domain)
            self.logger.info(f"Unmuted {username} in {channel}")
        except Exception as e:
            self.logger.debug(f"Could not unmute {username}: {e}")

    async def _handle_pattern_add(self, request: dict) -> dict:
        """Handle pattern.add command - Add a banned username pattern.

        Request:
            {
                "command": "pattern.add",
                "domain": "cytu.be",        # Optional
                "channel": "lounge",        # Required
                "pattern": "1488",          # Required
                "is_regex": false,          # Optional, default false
                "action": "ban",            # Optional, default "ban"
                "description": "Nazi hate"  # Optional
            }
        """
        channel = request.get("channel")
        pattern = request.get("pattern")
        is_regex = request.get("is_regex", False)
        action = request.get("action", "ban")
        description = request.get("description")
        added_by = request.get("added_by", "cli")

        domain = request.get("domain")
        if not domain:
            channels = self.app.config.get("channels", [])
            if channels:
                domain = channels[0].get("domain", "cytu.be")
            else:
                domain = "cytu.be"

        if not channel:
            raise ValueError("channel is required")
        if not pattern:
            raise ValueError("pattern is required")
        if action not in ("ban", "smute", "mute"):
            raise ValueError("action must be 'ban', 'smute', or 'mute'")

        if not self.app.pattern_managers:
            raise ValueError("Pattern matching is not enabled")

        pattern_manager = await self.app.pattern_managers.get_manager(domain, channel)

        entry = await pattern_manager.add(
            pattern=pattern,
            is_regex=is_regex,
            action=action,
            added_by=added_by,
            description=description,
        )

        return {
            "pattern": entry.pattern,
            "is_regex": entry.is_regex,
            "action": entry.action,
            "added_by": entry.added_by,
            "description": entry.description,
            "timestamp": entry.timestamp,
            "channel": channel,
            "domain": domain,
        }

    async def _handle_pattern_remove(self, request: dict) -> dict:
        """Handle pattern.remove command - Remove a banned username pattern.

        Request:
            {
                "command": "pattern.remove",
                "domain": "cytu.be",        # Optional
                "channel": "lounge",        # Required
                "pattern": "1488"           # Required
            }
        """
        channel = request.get("channel")
        pattern = request.get("pattern")

        domain = request.get("domain")
        if not domain:
            channels = self.app.config.get("channels", [])
            if channels:
                domain = channels[0].get("domain", "cytu.be")
            else:
                domain = "cytu.be"

        if not channel:
            raise ValueError("channel is required")
        if not pattern:
            raise ValueError("pattern is required")

        if not self.app.pattern_managers:
            raise ValueError("Pattern matching is not enabled")

        pattern_manager = await self.app.pattern_managers.get_manager(domain, channel)
        removed = await pattern_manager.remove(pattern)

        if not removed:
            raise ValueError(f"Pattern '{pattern}' not found in {channel}")

        return {
            "pattern": pattern,
            "channel": channel,
            "domain": domain,
            "removed": True,
        }

    async def _handle_pattern_list(self, request: dict) -> dict:
        """Handle pattern.list command - List all banned username patterns.

        Request:
            {
                "command": "pattern.list",
                "domain": "cytu.be",        # Optional
                "channel": "lounge"         # Required
            }
        """
        channel = request.get("channel")

        domain = request.get("domain")
        if not domain:
            channels = self.app.config.get("channels", [])
            if channels:
                domain = channels[0].get("domain", "cytu.be")
            else:
                domain = "cytu.be"

        if not channel:
            raise ValueError("channel is required")

        if not self.app.pattern_managers:
            raise ValueError("Pattern matching is not enabled")

        pattern_manager = await self.app.pattern_managers.get_manager(domain, channel)
        entries = await pattern_manager.list_all()

        return {
            "channel": channel,
            "domain": domain,
            "count": len(entries),
            "patterns": [
                {
                    "pattern": e.pattern,
                    "is_regex": e.is_regex,
                    "action": e.action,
                    "added_by": e.added_by,
                    "description": e.description,
                    "timestamp": e.timestamp,
                }
                for e in entries
            ],
        }
