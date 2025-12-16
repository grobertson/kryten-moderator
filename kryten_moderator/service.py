"""Main moderator service application."""

import asyncio
import json
import logging
import signal
import time
from pathlib import Path
from typing import Any

from kryten import (
    ChatMessageEvent,
    KrytenClient,
    UserJoinEvent,
    UserLeaveEvent,
)

from .ip_manager import IPManager, IPManagerRegistry, extract_ip_from_event
from .metrics_server import MetricsServer
from .moderation_list import ModerationEntry, ModerationListManager
from .nats_handler import ModeratorCommandHandler
from .pattern_manager import PatternManagerRegistry


class ModeratorService:
    """Kryten Moderator Service.

    Provides chat moderation capabilities for CyTube channels:
    - Chat message monitoring
    - User join/leave tracking
    - Spam detection (future)
    - Word filtering (future)
    - Rate limiting (future)
    """

    def __init__(self, config_path: str):
        """Initialize the service.

        Args:
            config_path: Path to configuration JSON file
        """
        self.config_path = Path(config_path)
        self.logger = logging.getLogger(__name__)

        # Components
        self.client: KrytenClient | None = None
        self.command_handler: ModeratorCommandHandler | None = None
        self.metrics_server: MetricsServer | None = None
        self.moderation_lists: ModerationListManager | None = None
        self.ip_managers: IPManagerRegistry | None = None
        self.pattern_managers: PatternManagerRegistry | None = None

        # State
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._start_time: float | None = None
        self._domain: str = "cytu.be"

        # Statistics counters
        self._events_processed = 0
        self._commands_processed = 0
        self._messages_checked = 0
        self._messages_flagged = 0
        self._users_tracked: set[str] = set()

        # Moderation enforcement counters
        self._bans_enforced = 0
        self._smutes_enforced = 0
        self._mutes_enforced = 0
        self._ip_correlations = 0
        self._pattern_matches = 0

        # Load configuration
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from file."""
        with open(self.config_path, encoding="utf-8") as f:
            self.config = json.load(f)

        # Override version from package to ensure it stays in sync
        from . import __version__
        if "service" not in self.config:
            self.config["service"] = {}
        self.config["service"]["version"] = __version__

        # Extract domain from first channel config
        channels = self.config.get("channels", [])
        if channels:
            self._domain = channels[0].get("domain", "cytu.be")

        self.logger.info(f"Configuration loaded from {self.config_path}")
        self.logger.info(f"Service version: {__version__}")

    async def start(self) -> None:
        """Start the service."""
        self.logger.info("Starting Kryten Moderator Service")

        # Initialize Kryten client
        self.client = KrytenClient(self.config)

        # Register event handlers
        self.logger.info("Registering event handlers...")

        @self.client.on("chatmsg")
        async def handle_chat(event: ChatMessageEvent):
            await self._handle_chat_message(event)

        @self.client.on("adduser")
        async def handle_user_join(event: UserJoinEvent):
            await self._handle_user_join(event)

        @self.client.on("userleave")
        async def handle_user_leave(event: UserLeaveEvent):
            await self._handle_user_leave(event)

        self.logger.info(f"Registered {len(self.client._handlers)} event types with handlers")

        # Connect to NATS (lifecycle events handled automatically via ServiceConfig)
        await self.client.connect()

        # Track start time for uptime
        self._start_time = time.time()

        # Initialize moderation lists for all configured channels
        self.moderation_lists = ModerationListManager(self.client)
        channels = self.config.get("channels", [])
        await self.moderation_lists.initialize_all(channels)
        self.logger.info(
            f"Moderation lists ready: {self.moderation_lists.list_count} channels, "
            f"{self.moderation_lists.total_entries} entries"
        )

        # Initialize IP managers for IP correlation (if enabled)
        mod_config = self.config.get("moderation", {})
        if mod_config.get("enable_ip_correlation", True):
            self.ip_managers = IPManagerRegistry(self.client)
            await self.ip_managers.initialize_all(channels)
            self.logger.info(
                f"IP correlation enabled: {self.ip_managers.manager_count} channels, "
                f"{self.ip_managers.total_mappings} mappings"
            )
        else:
            self.logger.info("IP correlation disabled in config")

        # Initialize pattern managers for username pattern matching (if enabled)
        if mod_config.get("enable_pattern_matching", True):
            default_patterns = mod_config.get("default_patterns", [])
            self.pattern_managers = PatternManagerRegistry(self.client)
            await self.pattern_managers.initialize_all(channels, default_patterns)
            self.logger.info(
                f"Pattern matching enabled: {self.pattern_managers.manager_count} channels, "
                f"{self.pattern_managers.total_patterns} patterns"
            )
        else:
            self.logger.info("Pattern matching disabled in config")

        # Lifecycle is now managed by KrytenClient - log confirmation
        if self.client.lifecycle:
            self.logger.info("Lifecycle publisher initialized via KrytenClient")

        # Subscribe to robot startup - re-announce when robot starts
        await self.client.subscribe("kryten.lifecycle.robot.startup", self._handle_robot_startup)
        self.logger.info("Subscribed to kryten.lifecycle.robot.startup")

        # Initialize command handler for NATS queries using existing KrytenClient
        self.command_handler = ModeratorCommandHandler(self, self.client)
        await self.command_handler.connect()

        # Initialize metrics server
        metrics_port = self.config.get("metrics", {}).get("port", 28284)
        self.metrics_server = MetricsServer(self, metrics_port)
        await self.metrics_server.start()

        # Start event processing
        self._running = True
        await self.client.run()

    async def stop(self) -> None:
        """Stop the service gracefully."""
        if not self._running:
            self.logger.debug("Service not running, skip stop")
            return

        self.logger.info("Stopping Kryten Moderator Service")
        self._running = False

        # Stop client event loop first
        if self.client:
            self.logger.debug("Stopping Kryten client...")
            await self.client.stop()

        # Stop command handler
        if self.command_handler:
            self.logger.debug("Disconnecting command handler...")
            await self.command_handler.disconnect()

        # Stop metrics server
        if self.metrics_server:
            self.logger.debug("Stopping metrics server...")
            await self.metrics_server.stop()

        # Disconnect from NATS
        if self.client:
            self.logger.debug("Disconnecting from NATS...")
            await self.client.disconnect()

        self.logger.info("Kryten Moderator Service stopped cleanly")

    async def _handle_robot_startup(self, event: Any) -> None:  # noqa: ARG002
        """Handle robot startup event to re-register with the ecosystem."""
        self._events_processed += 1
        self.logger.info("Received robot startup notification, re-announcing service...")

        # Re-announce via lifecycle if available
        if self.client and self.client.lifecycle:
            await self.client.lifecycle.publish_startup()
            self.logger.info("Re-announced service startup")

    async def _handle_chat_message(self, event: ChatMessageEvent) -> None:
        """Handle chat message event for moderation checks."""
        self._events_processed += 1
        self._messages_checked += 1

        try:
            # Safe message preview for logging
            msg_preview = (event.message or "")[:50] if event.message else "(no message)"
            self.logger.debug(f"Chat message from {event.username}: {msg_preview}")

            # Track user
            self._users_tracked.add(event.username.lower())

            # TODO: Add moderation checks here:
            # - Spam detection
            # - Banned word filtering
            # - Excessive caps detection
            # - URL filtering
            # - Rate limiting

            # Placeholder for future moderation logic
            # if self._check_spam(event):
            #     self._messages_flagged += 1
            #     await self._take_action(event, "spam")

        except Exception as e:
            self.logger.error(f"Error handling chat message: {e}", exc_info=True)

    async def _handle_user_join(self, event: UserJoinEvent) -> None:
        """Handle user join event with moderation enforcement."""
        self._events_processed += 1

        try:
            self.logger.debug(f"User joined: {event.username} in {event.channel}")

            # Track user
            self._users_tracked.add(event.username.lower())

            domain = getattr(event, 'domain', self._domain)

            # Extract IP from event (may be None if not available)
            full_ip, masked_ip = extract_ip_from_event(event)
            ip = full_ip or masked_ip  # Prefer full IP

            # Check moderation list for this channel
            if self.moderation_lists:
                entry = self.moderation_lists.check_username(
                    domain, event.channel, event.username
                )

                if entry:
                    # User is directly on moderation list
                    # Store their IP with the entry if we have one
                    if ip:
                        await self._add_ip_to_entry(domain, event.channel, event.username, ip)
                    await self._enforce_moderation(event, entry)
                    return

            # IP correlation check (if enabled and we have an IP)
            if self.ip_managers and ip and self.moderation_lists:
                ip_manager = self.ip_managers.get_manager_sync(domain, event.channel)
                mod_list = self.moderation_lists._lists.get(f"{domain}/{event.channel}")

                if ip_manager and mod_list:
                    # Check if this IP is associated with a moderated user
                    match = ip_manager.check_ip_correlation(
                        ip,
                        mod_list,
                        exclude_username=event.username,
                    )

                    if match:
                        source_username, source_entry = match
                        await self._handle_ip_correlation(
                            event, domain, ip, source_username, source_entry
                        )
                        return

                # Register this IP for future correlation
                if ip_manager:
                    await ip_manager.add_ip(ip, event.username)

            # Pattern matching check (if enabled)
            if self.pattern_managers:
                pattern_manager = self.pattern_managers.get_manager_sync(domain, event.channel)

                if pattern_manager:
                    pattern_result = pattern_manager.check_username(event.username)

                    if pattern_result:
                        pattern_entry, matched_pattern = pattern_result
                        await self._handle_pattern_match(
                            event, domain, ip, pattern_entry, matched_pattern
                        )
                        return

        except Exception as e:
            self.logger.error(f"Error handling user join: {e}", exc_info=True)

    async def _add_ip_to_entry(
        self, domain: str, channel: str, username: str, ip: str
    ) -> None:
        """Add an IP to a user's moderation entry and IP map.

        Args:
            domain: CyTube domain
            channel: Channel name
            username: Username
            ip: IP address to add
        """
        try:
            # Update moderation entry with this IP
            if not self.moderation_lists:
                return
            mod_list = await self.moderation_lists.get_list(domain, channel)
            await mod_list.update_ips(username, ip)

            # Add to IP manager for correlation
            if self.ip_managers:
                ip_manager = await self.ip_managers.get_manager(domain, channel)
                await ip_manager.add_ip(ip, username)

        except Exception as e:
            self.logger.warning(f"Failed to update IPs for {username}: {e}")

    async def _handle_ip_correlation(
        self,
        event: UserJoinEvent,
        domain: str,
        ip: str,
        source_username: str,
        source_entry: ModerationEntry,
    ) -> None:
        """Handle detected IP correlation - apply same action to new account.

        Args:
            event: The user join event
            domain: CyTube domain
            ip: The matching IP address
            source_username: The moderated user this IP matches
            source_entry: The moderation entry for the source user
        """
        self._ip_correlations += 1

        masked_ip = IPManager._mask_ip(ip)
        self.logger.warning(
            f"IP CORRELATION DETECTED: {event.username} matches {source_username} "
            f"(IP: {masked_ip}, action: {source_entry.action})"
        )

        # Create new moderation entry linked to source
        if not self.moderation_lists:
            return
        mod_list = await self.moderation_lists.get_list(domain, event.channel)
        new_entry = await mod_list.add(
            username=event.username,
            action=source_entry.action,
            moderator="system:ip_correlation",
            reason=f"IP correlation with {source_username}: {source_entry.reason or 'N/A'}",
            ips=[ip],
            ip_correlation_source=source_username,
        )

        # Add IP to manager for this new user
        if self.ip_managers:
            ip_manager = await self.ip_managers.get_manager(domain, event.channel)
            await ip_manager.add_ip(ip, event.username)

        # Enforce the action
        await self._enforce_moderation(event, new_entry)

    async def _handle_pattern_match(
        self,
        event: UserJoinEvent,
        domain: str,
        ip: str | None,
        pattern_entry,
        matched_pattern: str,
    ) -> None:
        """Handle detected pattern match - apply action based on pattern config.

        Args:
            event: The user join event
            domain: CyTube domain
            ip: The user's IP (if available)
            pattern_entry: The PatternEntry that matched
            matched_pattern: The pattern string that matched
        """
        self._pattern_matches += 1

        self.logger.warning(
            f"PATTERN MATCH DETECTED: {event.username} matched pattern '{matched_pattern}' "
            f"(action: {pattern_entry.action})"
        )

        # Create moderation entry for this user
        if not self.moderation_lists:
            return
        mod_list = await self.moderation_lists.get_list(domain, event.channel)
        new_entry = await mod_list.add(
            username=event.username,
            action=pattern_entry.action,
            moderator="system:pattern_match",
            reason=f"Username matched pattern '{matched_pattern}': {pattern_entry.description or 'banned pattern'}",
            ips=[ip] if ip else [],
            pattern_match=matched_pattern,
        )

        # Add IP to manager if we have one
        if ip and self.ip_managers:
            ip_manager = await self.ip_managers.get_manager(domain, event.channel)
            await ip_manager.add_ip(ip, event.username)

        # Enforce the action
        await self._enforce_moderation(event, new_entry)

    async def _enforce_moderation(
        self,
        event: UserJoinEvent,
        entry: ModerationEntry,
    ) -> None:
        """Enforce moderation action on joining user.

        Args:
            event: The user join event
            entry: The moderation entry to enforce
        """
        username = event.username
        channel = event.channel
        domain = getattr(event, 'domain', self._domain)

        self.logger.info(
            f"Enforcing {entry.action} on {username} in {channel} "
            f"(reason: {entry.reason or 'N/A'})"
        )

        if not self.client:
            self.logger.error("Cannot enforce moderation: no client connected")
            return

        try:
            if entry.action == "ban":
                # Kick the user (CyTube ban is IP-based, kick removes them)
                await self.client.kick_user(channel, username, reason=entry.reason, domain=domain)
                self._bans_enforced += 1
                self.logger.warning(f"ENFORCED BAN: Kicked {username} from {channel}")

            elif entry.action == "smute":
                # Shadow mute - user doesn't know
                await self.client.shadow_mute_user(channel, username, domain=domain)
                self._smutes_enforced += 1
                self.logger.info(f"ENFORCED SMUTE: Shadow muted {username} in {channel}")

            elif entry.action == "mute":
                # Visible mute - user is notified
                await self.client.mute_user(channel, username, domain=domain)
                self._mutes_enforced += 1
                self.logger.info(f"ENFORCED MUTE: Muted {username} in {channel}")

        except Exception as e:
            self.logger.error(
                f"Failed to enforce {entry.action} on {username}: {e}",
                exc_info=True
            )

    async def _handle_user_leave(self, event: UserLeaveEvent) -> None:
        """Handle user leave event."""
        self._events_processed += 1

        try:
            self.logger.debug(f"User left: {event.username} from {event.channel}")

            # TODO: Add leave tracking:
            # - Log session duration
            # - Track leave patterns

        except Exception as e:
            self.logger.error(f"Error handling user leave: {e}", exc_info=True)

    def get_uptime_seconds(self) -> float:
        """Get service uptime in seconds."""
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time


async def main():
    """Main entry point."""
    import argparse
    import platform
    import sys

    # Parse arguments
    parser = argparse.ArgumentParser(description="Kryten Moderator Service for CyTube")
    parser.add_argument(
        "--config", help="Configuration file path (default: /etc/kryten/kryten-moderator/config.json or ./config.json)"
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    # Setup logging first so we can log errors during config validation
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    logger = logging.getLogger(__name__)

    # Determine config file path
    if args.config:
        config_path = Path(args.config)
    else:
        # Try default locations in order
        default_paths = [
            Path("/etc/kryten/kryten-moderator/config.json"),
            Path("config.json")
        ]

        config_path = None
        for path in default_paths:
            if path.exists() and path.is_file():
                config_path = path
                break

        if not config_path:
            logger.error("No configuration file found.")
            logger.error("  Searched:")
            for path in default_paths:
                logger.error(f"    - {path}")
            logger.error("  Use --config to specify a custom path.")
            sys.exit(1)

    # Validate config file exists
    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)

    if not config_path.is_file():
        logger.error(f"Configuration path is not a file: {config_path}")
        sys.exit(1)

    # Create service
    service = ModeratorService(str(config_path))

    # Setup signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, initiating shutdown...")
        shutdown_event.set()

    # Register signal handlers (platform-specific)
    if platform.system() != "Windows":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: signal_handler(s, None))
    else:
        # Windows uses traditional signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    # Run service
    try:
        # Start service in background task
        service_task = asyncio.create_task(service.start())

        # Wait for shutdown signal or KeyboardInterrupt
        try:
            await shutdown_event.wait()
        except KeyboardInterrupt:
            logger.info("Received KeyboardInterrupt, initiating shutdown...")

        # Stop the service
        await service.stop()

        # Cancel and wait for service task
        service_task.cancel()
        try:
            await service_task
        except asyncio.CancelledError:
            pass

        logger.info("Shutdown complete")

    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt during startup, shutting down...")
        await service.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        await service.stop()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
