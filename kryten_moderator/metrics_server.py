"""Prometheus metrics HTTP server for moderator service.

Uses BaseMetricsServer from kryten-py for the HTTP server infrastructure.
"""

from kryten import BaseMetricsServer


class MetricsServer(BaseMetricsServer):
    """HTTP server exposing Prometheus metrics on port 28284.

    Extends kryten-py's BaseMetricsServer with moderator-specific metrics.
    Default port 28284 (userstats uses 28282, leaving room for other services).
    """

    def __init__(self, app_reference, port: int = 28284):
        """Initialize metrics server.

        Args:
            app_reference: Reference to ModeratorService for accessing components
            port: HTTP port to listen on (default 28284)
        """
        super().__init__(
            service_name="moderator",
            port=port,
            client=app_reference.client,
        )
        self.app = app_reference

    async def _collect_custom_metrics(self) -> list[str]:
        """Collect moderator-specific metrics."""
        lines = []

        # Events processed
        lines.append("# HELP moderator_events_processed Total events processed")
        lines.append("# TYPE moderator_events_processed counter")
        lines.append(f"moderator_events_processed {self.app._events_processed}")
        lines.append("")

        # Commands processed
        lines.append("# HELP moderator_commands_processed Total NATS commands processed")
        lines.append("# TYPE moderator_commands_processed counter")
        lines.append(f"moderator_commands_processed {self.app._commands_processed}")
        lines.append("")

        # Messages checked
        lines.append("# HELP moderator_messages_checked Total chat messages checked")
        lines.append("# TYPE moderator_messages_checked counter")
        lines.append(f"moderator_messages_checked {self.app._messages_checked}")
        lines.append("")

        # Messages flagged
        lines.append("# HELP moderator_messages_flagged Total messages flagged for moderation")
        lines.append("# TYPE moderator_messages_flagged counter")
        lines.append(f"moderator_messages_flagged {self.app._messages_flagged}")
        lines.append("")

        # Users tracked
        lines.append("# HELP moderator_users_tracked Unique users tracked this session")
        lines.append("# TYPE moderator_users_tracked gauge")
        lines.append(f"moderator_users_tracked {len(self.app._users_tracked)}")
        lines.append("")

        return lines

    async def _get_health_details(self) -> dict:
        """Get moderator-specific health details."""
        details: dict[str, str | int | bool] = {}

        # Event statistics
        details["events_processed"] = self.app._events_processed
        details["commands_processed"] = self.app._commands_processed
        details["messages_checked"] = self.app._messages_checked
        details["messages_flagged"] = self.app._messages_flagged
        details["users_tracked"] = len(self.app._users_tracked)

        # Channel configuration
        details["channels_configured"] = len(self.app.config.get("channels", []))

        # Moderation features status
        moderation_config = self.app.config.get("moderation", {})
        details["spam_detection_enabled"] = moderation_config.get("enable_spam_detection", False)
        details["word_filter_enabled"] = moderation_config.get("enable_word_filter", False)
        details["rate_limiting_enabled"] = moderation_config.get("enable_rate_limiting", False)

        return details
