"""Command queue handlers for IRCBot."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dccbot.ircbot import IRCBot

logger = logging.getLogger(__name__)


async def handle_send_command(bot: IRCBot, data: dict[str, Any]) -> None:
    """Handle send command by joining channels and sending messages."""
    if not data.get("user") or not data.get("message"):
        return

    if data.get("channels"):
        await bot._join_channels(data["channels"])

    try:
        bot.connection.privmsg(data["user"], data["message"])
        logger.info("Sent message to %s: %s", data["user"], data["message"])
    except Exception as e:
        logger.error("Failed to send message to %s: %s", data["user"], e)

    if data.get("channels"):
        bot._update_channel_mapping(data["user"], data["channels"])


async def handle_part_command(bot: IRCBot, data: dict[str, Any]) -> None:
    """Handle part command by parting specified channels."""
    if data.get("channels"):
        for channel in data["channels"]:
            await bot.part_channel(channel, data.get("reason"))
