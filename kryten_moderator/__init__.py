"""Kryten Moderator Service - Chat moderation and filtering.

kryten-moderator is a microservice that provides chat moderation capabilities
for CyTube channels through the Kryten bridge, including:

- Spam detection and filtering
- Word/phrase filtering (banned words)
- User tracking (joins/leaves)
- Rate limiting
- Flood protection

It exposes control via:
- NATS request/reply endpoints (kryten.moderator.command)

For more information, see:
- README.md for setup and usage
- INSTALL.md for installation instructions
"""

__version__ = "0.3.0"
__author__ = "Kryten Contributors"
__license__ = "MIT"

from .service import ModeratorService

__all__ = ["ModeratorService"]
