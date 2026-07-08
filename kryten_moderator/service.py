"""Main moderator service application."""

import asyncio
import json
import logging
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kryten import (
    ChatMessageEvent,
    KrytenClient,
    UserJoinEvent,
    UserLeaveEvent,
)

from .ip_manager import IPManager, IPManagerRegistry, extract_ip_from_event
from .ban_tombstones import ORIGIN_CYTUBE, ORIGIN_MODERATOR, TombstoneManager
from .metrics_server import MetricsServer
from .moderation_list import ModerationEntry, ModerationListManager
from .nats_handler import ModeratorCommandHandler
from .pattern_manager import PatternManagerRegistry
from .user_history import UserHistoryRegistry


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
        self.user_history: UserHistoryRegistry | None = None
        self.metrics_server: MetricsServer | None = None
        self.moderation_lists: ModerationListManager | None = None
        self.ip_managers: IPManagerRegistry | None = None
        self.pattern_managers: PatternManagerRegistry | None = None
        self.tombstones: TombstoneManager | None = None

        # State
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._start_time: float | None = None
        self._domain: str = "cytu.be"
        self._reconcile_task: asyncio.Task | None = None
        self._ban_sync_interval: int = 60

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

        @self.client.on("banlist")
        async def handle_banlist(event: Any):
            await self._handle_banlist_event(event)

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

        # Initialize ban tombstone tracking for bidirectional reconciliation
        mod_config = self.config.get("moderation", {})
        self._ban_sync_interval = int(mod_config.get("ban_sync_interval_seconds", 60))
        tombstone_ttl = int(mod_config.get("tombstone_ttl_minutes", 15)) * 60
        self.tombstones = TombstoneManager(self.client, ttl_seconds=tombstone_ttl)
        for ch in channels:
            ch_name = ch.get("channel")
            ch_domain = ch.get("domain", self._domain)
            if ch_name:
                await self.tombstones.get_list(ch_domain, ch_name)

        # Sync Cytube ban list into moderator's list on startup
        for ch in channels:
            ch_name = ch.get("channel")
            ch_domain = ch.get("domain", self._domain)
            if ch_name:
                try:
                    await self.client.request_banlist(ch_name, domain=ch_domain)
                    self.logger.info(f"Requested Cytube ban list for {ch_domain}/{ch_name}")
                except Exception as e:
                    self.logger.warning(f"Could not request ban list for {ch_name}: {e}")

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

        # Initialize user connection history (in-memory, per-channel)
        retention_hours = mod_config.get("history_retention_hours", 12)
        self.user_history = UserHistoryRegistry(retention_seconds=retention_hours * 3600)
        self.user_history.initialize_all(channels)
        self.logger.info(f"User history tracking initialized: {retention_hours}h retention")

        # Seed user history and enforce moderation for users already in the channel
        for ch in channels:
            ch_name = ch.get("channel")
            ch_domain = ch.get("domain", self._domain)
            if ch_name:
                await self._seed_from_userlist(ch_domain, ch_name)

        # Initialize metrics server
        metrics_port = self.config.get("metrics", {}).get("port", 28284)
        self.metrics_server = MetricsServer(self, metrics_port)
        await self.metrics_server.start()

        # Start event processing
        self._running = True

        # Start periodic ban-list reconciliation loop
        self._reconcile_task = asyncio.create_task(self._ban_reconcile_loop())
        self.logger.info(
            f"Ban-list reconciliation loop started (interval: {self._ban_sync_interval}s)"
        )

        await self.client.run()

    async def stop(self) -> None:
        """Stop the service gracefully."""
        if not self._running:
            self.logger.debug("Service not running, skip stop")
            return

        self.logger.info("Stopping Kryten Moderator Service")
        self._running = False

        # Stop periodic reconciliation loop
        if self._reconcile_task:
            self._reconcile_task.cancel()
            try:
                await self._reconcile_task
            except asyncio.CancelledError:
                pass
            self._reconcile_task = None

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

        # Re-seed from live userlist — users may have changed while robot was down
        channels = self.config.get("channels", [])
        for ch in channels:
            ch_name = ch.get("channel")
            ch_domain = ch.get("domain", self._domain)
            if ch_name:
                await self._seed_from_userlist(ch_domain, ch_name)

    async def _get_online_users(self, domain: str, channel: str) -> list[dict]:
        """Return the current user list from the robot's KV store.

        The kryten-robot maintains a live snapshot at:
          bucket  cytube_{safe_domain}_{channel}_userlist
          key     users

        Returns an empty list if the bucket is unavailable or empty.
        """
        if not self.client:
            return []
        safe_domain = domain.lower().replace(".", "_")
        bucket = f"cytube_{safe_domain}_{channel.lower()}_userlist"
        try:
            return await self.client.kv_get(bucket, "users", default=[], parse_json=True)
        except Exception as e:
            self.logger.debug(f"Could not read userlist KV {bucket}: {e}")
            return []

    async def _seed_from_userlist(self, domain: str, channel: str) -> None:
        """Seed user history and enforce moderation for all users currently in channel.

        Reads the live user list that kryten-robot stores in NATS KV and passes
        each user through the standard join-time enforcement path so that bans
        and patterns are applied to users who were already present when the
        moderator started (or when the robot reconnected).
        """
        users = await self._get_online_users(domain, channel)
        if not users:
            self.logger.info(f"No users in KV userlist for {domain}/{channel}")
            return

        self.logger.info(
            f"Seeding {len(users)} user(s) from KV userlist for {domain}/{channel}"
        )
        now = datetime.now(timezone.utc)
        for user_data in users:
            if not isinstance(user_data, dict):
                continue
            username = user_data.get("name", "")
            if not username:
                continue
            synthetic = UserJoinEvent(
                username=username,
                rank=user_data.get("rank", 0),
                timestamp=now,
                channel=channel,
                domain=domain,
                correlation_id="startup:userlist_seed",
            )
            await self._handle_user_join(synthetic)

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

            # Record message in user history
            if self.user_history:
                channel = getattr(event, "channel", None)
                domain = getattr(event, "domain", self._domain)
                if channel:
                    self.user_history.on_message(domain, channel, event.username)

        except Exception as e:
            self.logger.error(f"Error handling chat message: {e}", exc_info=True)

    async def _handle_user_join(self, event: UserJoinEvent) -> None:
        """Handle user join event with moderation enforcement."""
        self._events_processed += 1

        try:
            self.logger.debug(f"User joined: {event.username} in {event.channel}")

            # Track user
            self._users_tracked.add(event.username.lower())

            domain = getattr(event, "domain", self._domain)

            # Extract IP from event (may be None if not available)
            full_ip, masked_ip = extract_ip_from_event(event)
            ip = full_ip or masked_ip  # Prefer full IP

            # Record join in user history (masked IP only — history is exposed externally)
            if self.user_history:
                self.user_history.on_join(domain, event.channel, event.username, masked_ip)

            # Check moderation list for this channel
            if self.moderation_lists:
                entry = self.moderation_lists.check_username(domain, event.channel, event.username)

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

    async def _add_ip_to_entry(self, domain: str, channel: str, username: str, ip: str) -> None:
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
        domain = getattr(event, "domain", self._domain)

        self.logger.info(
            f"Enforcing {entry.action} on {username} in {channel} "
            f"(reason: {entry.reason or 'N/A'})"
        )

        if not self.client:
            self.logger.error("Cannot enforce moderation: no client connected")
            return

        try:
            if entry.action == "ban":
                # client.ban_user() sends command:"ban" — the robot has a direct handler
                # for this. Confirmed via live NATS capture of the dispatch table.
                await self.client.ban_user(channel, username, reason=entry.reason, domain=domain)
                self._bans_enforced += 1
                self.logger.warning(f"ENFORCED BAN: Banned {username} from {channel}")

            elif entry.action == "smute":
                # client.shadow_mute_user() sends command:"chat" which the robot ignores.
                # Use send_command with the robot's direct "smute" handler instead.
                await self.client.send_command(
                    "robot", "smute", {"name": username}, domain=domain, channel=channel
                )
                self._smutes_enforced += 1
                self.logger.info(f"ENFORCED SMUTE: Shadow muted {username} in {channel}")

            elif entry.action == "mute":
                # client.mute_user() sends command:"chat" which the robot ignores.
                # Use send_command with the robot's direct "mute" handler instead.
                await self.client.send_command(
                    "robot", "mute", {"name": username}, domain=domain, channel=channel
                )
                self._mutes_enforced += 1
                self.logger.info(f"ENFORCED MUTE: Muted {username} in {channel}")

        except Exception as e:
            self.logger.error(f"Failed to enforce {entry.action} on {username}: {e}", exc_info=True)

    async def _ban_reconcile_loop(self) -> None:
        """Periodically request each channel's Cytube ban list to drive reconcile.

        The actual diff happens in ``_handle_banlist_event`` when the ``banlist``
        snapshot arrives over NATS. This loop just triggers the request on a
        fixed interval so that removals on either side converge.
        """
        try:
            while self._running:
                await asyncio.sleep(self._ban_sync_interval)
                if not self._running or not self.client:
                    break
                channels = self.config.get("channels", [])
                for ch in channels:
                    ch_name = ch.get("channel")
                    ch_domain = ch.get("domain", self._domain)
                    if not ch_name:
                        continue
                    try:
                        await self.client.request_banlist(ch_name, domain=ch_domain)
                    except Exception as e:  # noqa: BLE001
                        self.logger.debug(
                            f"Periodic ban-list request failed for {ch_name}: {e}"
                        )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.logger.error(f"Ban reconcile loop crashed: {e}", exc_info=True)

    async def _handle_banlist_event(self, event: Any) -> None:
        """Reconcile the Cytube ban list with the moderator's list (bidirectional).

        Triggered by every ``banlist`` snapshot (startup, periodic loop, or any
        service issuing requestBanlist). Reconciliation rules (bans only — mutes
        and smutes never appear in Cytube's ban list):

        - present both sides -> in sync; clear any tombstone; mark cytube_seen.
        - moderator-only, previously seen on Cytube -> removed on Cytube; delete
          the moderator entry.
        - moderator-only, never seen on Cytube -> new moderator ban; enforce it
          on Cytube.
        - Cytube-only with a moderator tombstone -> the moderator removed it;
          re-send the unban and do NOT re-import (defeats the resurrection race).
        - Cytube-only with no tombstone -> Cytube-originated ban; import it.
        - absent both sides -> clear any stale tombstone.

        Args:
            event: RawEvent whose payload is the Cytube ban list array.
        """
        try:
            domain = getattr(event, "domain", self._domain)
            channel = getattr(event, "channel", None)
            if not channel:
                self.logger.debug("banlist event has no channel, skipping")
                return

            payload = getattr(event, "payload", None)
            if not isinstance(payload, list):
                self.logger.debug(
                    f"banlist payload is {type(payload).__name__}, expected list — skipping"
                )
                return

            if not self.moderation_lists or self.tombstones is None:
                return

            mod_list = await self.moderation_lists.get_list(domain, channel)
            tombstones = await self.tombstones.get_list(domain, channel)
            await tombstones.prune()

            # Index Cytube bans by lowercased name.
            cytube_by_name: dict[str, dict] = {}
            for ban in payload:
                if not isinstance(ban, dict):
                    continue
                name = ban.get("name")
                if name:
                    cytube_by_name[name.lower()] = ban

            # Index moderator BAN entries by lowercased username.
            mod_bans = {
                e.username.lower(): e
                for e in await mod_list.list_all(filter_action="ban")
            }

            imported: list[str] = []

            for key in set(cytube_by_name) | set(mod_bans):
                cytube_ban = cytube_by_name.get(key)
                entry = mod_bans.get(key)
                tombstone = tombstones.get(key)

                if entry and cytube_ban:
                    # In sync on both sides.
                    await mod_list.mark_cytube_seen(entry.username, True)
                    if tombstone:
                        await tombstones.remove(key)

                elif entry and not cytube_ban:
                    if entry.cytube_seen:
                        # Was on Cytube before, now gone -> unbanned on Cytube.
                        await mod_list.remove(entry.username)
                        # Tombstone so a stale snapshot still listing the ban does
                        # not re-import it before it ages out.
                        await tombstones.add(entry.username, ORIGIN_CYTUBE)
                        self.logger.info(
                            f"Ban for {entry.username} removed on Cytube — "
                            f"deleted moderator entry ({domain}/{channel})"
                        )
                        await self._emit_event(
                            "enforcement.removed",
                            {
                                "username": entry.username,
                                "channel": channel,
                                "domain": domain,
                                "source": "cytube_unban",
                            },
                        )
                    else:
                        # Moderator ban not yet enforced on Cytube -> push it.
                        self.logger.info(
                            f"Enforcing moderator ban for {entry.username} on Cytube "
                            f"({domain}/{channel})"
                        )
                        await self._push_ban_to_cytube(
                            domain, channel, entry.username, entry.reason
                        )

                elif cytube_ban and not entry:
                    if tombstone and tombstone.origin == ORIGIN_MODERATOR:
                        # Moderator removed it; Cytube still lists it -> re-send unban.
                        self.logger.info(
                            f"Ban for {cytube_ban.get('name')} still on Cytube after "
                            f"moderator removal — re-sending unban ({domain}/{channel})"
                        )
                        await self._unban_on_cytube(
                            domain, channel, cytube_ban.get("name", key)
                        )
                    elif tombstone and tombstone.origin == ORIGIN_CYTUBE:
                        # Removed on Cytube already; this is a stale snapshot still
                        # listing it. Suppress re-import until the tombstone ages out.
                        self.logger.debug(
                            f"Ignoring stale Cytube ban for {cytube_ban.get('name')} "
                            f"(recently removed on Cytube)"
                        )
                    else:
                        # Cytube-originated ban -> import into moderator list.
                        username = cytube_ban.get("name")
                        ip = cytube_ban.get("ip")
                        await mod_list.add(
                            username=username,
                            action="ban",
                            moderator=f"system:cytube_sync:{cytube_ban.get('bannedby', 'unknown')}",
                            reason=cytube_ban.get("reason"),
                            ips=[ip] if ip else [],
                            cytube_seen=True,
                        )
                        imported.append(username)

                else:
                    # Absent both sides — clear any stale tombstone.
                    if tombstone:
                        await tombstones.remove(key)

            if imported:
                self.logger.info(
                    f"Imported {len(imported)} Cytube ban(s) into moderator list "
                    f"for {domain}/{channel}"
                )
                # Enforce newly imported bans on users currently in the channel.
                online = await self._get_online_users(domain, channel)
                imported_set = {u.lower() for u in imported}
                for user_data in online:
                    if not isinstance(user_data, dict):
                        continue
                    online_name = user_data.get("name", "")
                    if not online_name or online_name.lower() not in imported_set:
                        continue
                    entry = await mod_list.get(online_name)
                    if not entry:
                        continue
                    synthetic = UserJoinEvent(
                        username=online_name,
                        rank=user_data.get("rank", 0),
                        timestamp=datetime.now(timezone.utc),
                        channel=channel,
                        domain=domain,
                        correlation_id="banlist_sync:online_enforce",
                    )
                    await self._enforce_moderation(synthetic, entry)

        except Exception as e:
            self.logger.error(f"Error handling banlist event: {e}", exc_info=True)

    async def _push_ban_to_cytube(
        self, domain: str, channel: str, username: str, reason: str | None
    ) -> None:
        """Enforce a moderator-originated ban on Cytube via the robot."""
        if not self.client:
            return
        try:
            await self.client.ban_user(channel, username, reason=reason, domain=domain)
            self._bans_enforced += 1
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Could not push ban for {username} to Cytube: {e}")

    async def _unban_on_cytube(self, domain: str, channel: str, username: str) -> None:
        """Send an unban to the robot for a moderator-removed ban."""
        if not self.client:
            return
        try:
            await self.client.send_command(
                "robot", "unban", {"username": username}, domain=domain, channel=channel
            )
        except Exception as e:  # noqa: BLE001
            self.logger.debug(f"Could not send unban for {username} to Cytube: {e}")

    async def _emit_event(self, suffix: str, data: dict) -> None:
        """Publish a moderator enforcement event (stub — not yet wired).

        Mirrors ModeratorCommandHandler._emit_event. Bidirectional NATS event
        emission is planned for kryten-api-gate integration; until then this is
        a no-op so reconciliation does not depend on an unwired subject.
        """
        # TODO(0.8.0): publish to kryten.moderator.event.<suffix> for REST push support
        self.logger.debug(f"[event:{suffix}] {data}")

    async def _handle_user_leave(self, event: UserLeaveEvent) -> None:
        """Handle user leave event."""
        self._events_processed += 1

        try:
            self.logger.debug(f"User left: {event.username} from {event.channel}")

            # Close history session for departing user
            if self.user_history:
                domain = getattr(event, "domain", self._domain)
                self.user_history.on_leave(domain, event.channel, event.username)

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
        "--config",
        help="Configuration file path (default: /etc/kryten/kryten-moderator/config.json or ./config.json)",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    # Setup logging first so we can log errors during config validation
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

    # Determine config file path
    if args.config:
        config_path = Path(args.config)
    else:
        # Try default locations in order
        default_paths = [Path("/etc/kryten/kryten-moderator/config.json"), Path("config.json")]

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
