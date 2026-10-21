"""Tests for the command_pipeline module."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from dccbot.command_pipeline import handle_nick_command, handle_send_command


@pytest.mark.asyncio
async def test_handle_send_command_sends_privmsg_and_maps_channel():
    """Test handle_send_command joins channels, sends the message, and maps the channel."""
    bot = MagicMock()
    bot._join_channels = AsyncMock()
    data = {"user": "MyUser", "message": "Hello", "channels": ["#test"]}

    await handle_send_command(bot, data)

    bot._join_channels.assert_awaited_once_with(["#test"])
    bot.connection.privmsg.assert_called_once_with("myuser", "Hello")
    bot._update_channel_mapping.assert_called_once_with("myuser", ["#test"])


@pytest.mark.asyncio
async def test_handle_send_command_lowercases_and_strips_user():
    """Test handle_send_command normalizes the target nick in-place on `data`."""
    bot = MagicMock()
    data = {"user": "  MyUser  ", "message": "Hello"}

    await handle_send_command(bot, data)

    assert data["user"] == "myuser"
    bot.connection.privmsg.assert_called_once_with("myuser", "Hello")


@pytest.mark.asyncio
async def test_handle_send_command_no_user():
    """Test handle_send_command does nothing without a user."""
    bot = MagicMock()
    data = {"message": "Hello"}

    await handle_send_command(bot, data)

    bot.connection.privmsg.assert_not_called()


@pytest.mark.asyncio
async def test_handle_send_command_no_message():
    """Test handle_send_command does nothing without a message."""
    bot = MagicMock()
    data = {"user": "MyUser"}

    await handle_send_command(bot, data)

    bot.connection.privmsg.assert_not_called()


@pytest.mark.asyncio
async def test_handle_send_command_privmsg_exception():
    """Test handle_send_command logs and continues if privmsg raises."""
    bot = MagicMock()
    bot._join_channels = AsyncMock()
    bot.connection.privmsg.side_effect = RuntimeError("send failed")
    data = {"user": "MyUser", "message": "Hello", "channels": ["#test"]}

    await handle_send_command(bot, data)

    bot.connection.privmsg.assert_called_once_with("myuser", "Hello")
    bot._update_channel_mapping.assert_called_once_with("myuser", ["#test"])


@pytest.mark.asyncio
async def test_handle_send_command_no_channels_skips_join_and_mapping():
    """Test handle_send_command skips joining/mapping when no channels are given."""
    bot = MagicMock()
    bot._join_channels = AsyncMock()
    data = {"user": "MyUser", "message": "Hello"}

    await handle_send_command(bot, data)

    bot._join_channels.assert_not_awaited()
    bot._update_channel_mapping.assert_not_called()


@pytest.mark.asyncio
async def test_handle_nick_command_sends_nick():
    """Test handle_nick_command sends a NICK request to the server."""
    bot = MagicMock()
    bot.server = "irc.example.net"

    await handle_nick_command(bot, {"nick": "NewBot9"})

    bot.connection.nick.assert_called_once_with("NewBot9")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nick",
    [
        "",
        "bad nick",
        "9starts-digit",
        "has space",
        "x" * 31,
    ],
)
async def test_handle_nick_command_rejects_invalid(nick):
    """Test handle_nick_command ignores malformed nicknames."""
    bot = MagicMock()

    await handle_nick_command(bot, {"nick": nick})

    bot.connection.nick.assert_not_called()


@pytest.mark.asyncio
async def test_handle_nick_command_nick_exception():
    """Test handle_nick_command logs and swallows a connection failure."""
    bot = MagicMock()
    bot.server = "irc.example.net"
    bot.connection.nick.side_effect = RuntimeError("send failed")

    await handle_nick_command(bot, {"nick": "NewBot9"})

    bot.connection.nick.assert_called_once_with("NewBot9")
