"""Command queue handlers for IRCBot."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dccbot.ircbot import IRCBot

logger = logging.getLogger(__name__)

# RFC 2812 nick pattern: letter/special first, then letters, digits, specials,
# hyphens; capped at 30 chars. Servers may impose a stricter limit and reject
# with ERR_ERRONEUSNICKNAME (432) or ERR_NICKNAMEINUSE (433).
NICK_RE = re.compile(r"^[A-Za-z\[\]\\`_^{|}][A-Za-z0-9\[\]\\`_^{|}-]{0,29}$")


async def handle_send_command(bot: IRCBot, data: dict[str, Any]) -> None:
    """Handle send command by joining channels and sending messages."""
    if not data.get("user") or not data.get("message"):
        return

    user = data["user"].lower().strip()
    data["user"] = user

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


async def handle_nick_command(bot: IRCBot, data: dict[str, Any]) -> None:
    """Handle nick command by sending a NICK request to the server."""
    nick = (data.get("nick") or "").strip()
    if not NICK_RE.match(nick):
        logger.warning("Ignoring invalid nickname change request: %r", nick)
        return
    try:
        bot.connection.nick(nick)
        logger.info("Requested nick change to %s on %s", nick, bot.server)
    except Exception as e:
        logger.error("Failed to change nick to %s on %s: %s", nick, bot.server, e)
