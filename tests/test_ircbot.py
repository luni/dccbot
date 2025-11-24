"""Tests for IRCBot class."""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dccbot.ircbot import IRCBot


@pytest.fixture
def mock_bot_manager():
    """Create a mock bot manager."""
    manager = MagicMock()
    manager.config = {}
    return manager


@pytest.fixture
def bot_factory(mock_bot_manager, loop_patch):
    """Factory to build IRCBot instances with shared defaults."""

    def _create_bot(
        *,
        server_config: dict | None = None,
        allowed_mimetypes: list[str] | None = None,
        max_file_size: int = 1_000_000,
        download_path: str = "/tmp/downloads",
        manager=None,
    ) -> IRCBot:
        base_config = {"nick": "testbot", "channels": ["#test"]}
        if server_config:
            base_config.update(server_config)

        return IRCBot(
            server="irc.example.com",
            server_config=base_config,
            download_path=download_path,
            allowed_mimetypes=allowed_mimetypes if allowed_mimetypes is not None else ["application/x-bittorrent"],
            max_file_size=max_file_size,
            bot_manager=manager or mock_bot_manager,
        )

    return _create_bot


@pytest.fixture
def bot(bot_factory):
    """Default IRCBot instance for tests."""

    return bot_factory()


def test_ircbot_initialization(bot):
    """Test IRCBot initialization."""
    assert bot.server == "irc.example.com"
    assert bot.nick == "testbot"
    assert bot.download_path == "/tmp/downloads"
    assert bot.max_file_size == 1000000
    assert len(bot.joined_channels) == 0


def test_ircbot_random_nick(bot_factory):
    """Test IRCBot with random nick generation."""
    bot = bot_factory(server_config={"random_nick": True}, allowed_mimetypes=None)
    assert bot.nick.startswith("testbot")
    assert len(bot.nick) == len("testbot") + 3
    assert bot.nick[-3:].isdigit()


def test_get_version():
    """Test get_version static method."""
    version = IRCBot.get_version()
    assert "dccbot" in version.lower()


def test_generate_random_nick():
    """Test random nick generation."""
    nick = IRCBot._generate_random_nick("testbot")
    assert nick.startswith("testbot")
    assert len(nick) == len("testbot") + 3
    assert nick[-3:].isdigit()


@pytest.mark.asyncio
async def test_connect_without_tls(bot):
    """Test connection without TLS."""
    with patch("dccbot.ircbot.AioConnection") as mock_connection:
        mock_conn_instance = AsyncMock()
        mock_connection.return_value = mock_conn_instance

        await bot.connect()

        mock_conn_instance.connect.assert_called_once()
        call_args = mock_conn_instance.connect.call_args
        assert call_args[0][0] == "irc.example.com"
        assert call_args[0][1] == 6667
        assert call_args[0][2] == "testbot"


@pytest.mark.asyncio
async def test_connect_with_tls(bot_factory):
    """Test connection with TLS."""
    bot = bot_factory(server_config={"use_tls": True}, allowed_mimetypes=None)

    with patch("dccbot.ircbot.AioConnection") as mock_connection:
        mock_conn_instance = AsyncMock()
        mock_connection.return_value = mock_conn_instance

        await bot.connect()

        mock_conn_instance.connect.assert_called_once()
        call_args = mock_conn_instance.connect.call_args
        assert call_args[0][1] == 6697  # TLS port


@pytest.mark.asyncio
async def test_connect_with_custom_port(bot_factory):
    """Test connection with custom port."""
    bot = bot_factory(server_config={"port": 7000}, allowed_mimetypes=None)

    with patch("dccbot.ircbot.AioConnection") as mock_connection:
        mock_conn_instance = AsyncMock()
        mock_connection.return_value = mock_conn_instance

        await bot.connect()

        call_args = mock_conn_instance.connect.call_args
        assert call_args[0][1] == 7000


@pytest.mark.asyncio
async def test_disconnect(bot):
    """Test disconnect."""
    bot.connection = MagicMock()
    await bot.disconnect("Test reason")
    bot.connection.disconnect.assert_called_once_with("Test reason")


@pytest.mark.asyncio
async def test_join_channel(bot):
    """Test joining a channel."""
    bot.connection = MagicMock()
    await bot.join_channel("#test")
    bot.connection.join.assert_called_once_with("#test")


@pytest.mark.asyncio
async def test_join_channel_already_joined(bot):
    """Test joining a channel that's already joined."""
    bot.connection = MagicMock()
    bot.joined_channels["#test"] = 123456.0
    await bot.join_channel("#test")
    bot.connection.join.assert_not_called()


@pytest.mark.asyncio
async def test_join_channel_empty(bot):
    """Test joining an empty channel name."""
    bot.connection = MagicMock()
    await bot.join_channel("")
    bot.connection.join.assert_not_called()


@pytest.mark.asyncio
async def test_part_channel(bot):
    """Test parting a channel."""
    bot.connection = MagicMock()
    bot.joined_channels["#test"] = 123456.0
    await bot.part_channel("#test", "Goodbye")
    bot.connection.part.assert_called_once_with("#test", "Goodbye")
    assert "#test" not in bot.joined_channels


@pytest.mark.asyncio
async def test_part_channel_not_joined(bot):
    """Test parting a channel that's not joined."""
    bot.connection = MagicMock()
    await bot.part_channel("#test")
    bot.connection.part.assert_not_called()


@pytest.mark.asyncio
async def test_queue_command(bot):
    """Test queueing a command."""
    command = {"command": "join", "channels": ["#test"]}
    await bot.queue_command(command)
    queued = await bot.command_queue.get()
    assert queued == command


def test_is_valid_filename():
    """Test filename validation."""
    path = "/tmp/downloads"

    # Valid filename
    assert IRCBot.is_valid_filename(path, "test.txt") is True

    # Invalid: contains slash
    assert IRCBot.is_valid_filename(path, "test/file.txt") is False

    # Invalid: contains backslash
    assert IRCBot.is_valid_filename(path, "test\\file.txt") is False

    # Invalid: empty
    assert IRCBot.is_valid_filename(path, "") is False

    # Invalid: path traversal
    assert IRCBot.is_valid_filename(path, "../test.txt") is False


def test_on_welcome(bot):
    """Test on_welcome handler."""
    bot.connection = MagicMock()
    event = MagicMock()

    with patch("asyncio.create_task") as mock_create_task:
        bot.on_welcome(bot.connection, event)
        mock_create_task.assert_called_once()


def test_on_welcome_with_nickserv(bot_factory, mock_bot_manager):
    """Test on_welcome with NickServ authentication."""
    bot = bot_factory(server_config={"nickserv_password": "secret"}, allowed_mimetypes=None)
    bot.connection = MagicMock()
    event = MagicMock()

    with patch("asyncio.create_task"):
        bot.on_welcome(bot.connection, event)
        bot.connection.privmsg.assert_called_once_with("NickServ", "IDENTIFY secret")


def test_on_bannedfromchan(bot):
    """Test on_bannedfromchan handler."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.target = "#test"
    event.arguments = ["#test"]

    bot.on_bannedfromchan(bot.connection, event)
    assert "#test" in bot.banned_channels


def test_on_nochanmodes(bot):
    """Test on_nochanmodes handler."""
    bot.connection = MagicMock()
    bot.joined_channels["#test"] = 123456.0
    event = MagicMock()
    event.arguments = ["#test", "reason"]

    bot.on_nochanmodes(bot.connection, event)
    assert "#test" not in bot.joined_channels


def test_on_loggedin(bot):
    """Test on_loggedin handler."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.arguments = ["Logged in"]

    bot.on_loggedin(bot.connection, event)
    assert bot.authenticated is True
    assert bot.authenticated_event.is_set()


def test_on_privmsg_sets_auth_on_nickserv_message(bot):
    """Ensure NickServ success notices mark the bot as authenticated."""
    bot.authenticated = False
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "NickServ"
    event.arguments = ["Password accepted - you are now recognized."]

    bot.on_privmsg(bot.connection, event)

    assert bot.authenticated is True
    assert bot.authenticated_event.is_set()


def test_on_privmsg_ignores_nonmatching_message(bot):
    """Ensure other notices do not toggle authentication."""
    bot.authenticated = False
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "SomeOtherServ"
    event.arguments = ["Password accepted - you are now recognized."]

    bot.on_privmsg(bot.connection, event)

    assert bot.authenticated is False
    assert bot.authenticated_event.is_set() is False


def test_on_part(bot):
    """Test on_part handler."""
    bot.connection = MagicMock()
    bot.joined_channels["#test"] = 123456.0
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "testbot"
    event.target = "#test"
    event.arguments = []

    bot.on_part(bot.connection, event)
    assert "#test" not in bot.joined_channels


def test_on_part_other_user(bot):
    """Test on_part handler for other user."""
    bot.connection = MagicMock()
    bot.joined_channels["#test"] = 123456.0
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "otheruser"
    event.target = "#test"

    bot.on_part(bot.connection, event)
    assert "#test" in bot.joined_channels


def test_on_join(bot):
    """Test on_join handler."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "testbot"
    event.target = "#test"
    event.arguments = []

    bot.on_join(bot.connection, event)
    assert "#test" in bot.joined_channels


def test_on_join_other_user(bot):
    """Test on_join handler for other user."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "otheruser"
    event.target = "#test"

    bot.on_join(bot.connection, event)
    assert "#test" not in bot.joined_channels


def test_on_kick(bot):
    """Test on_kick handler."""
    bot.connection = MagicMock()
    bot.joined_channels["#test"] = 123456.0
    event = MagicMock()
    event.target = "#test"
    event.arguments = ["testbot", "reason"]

    bot.on_kick(bot.connection, event)
    assert "#test" not in bot.joined_channels


@pytest.mark.asyncio
async def test_handle_send_command(bot):
    """Test _handle_send_command."""
    bot.connection = MagicMock()
    data = {
        "user": "testuser",
        "message": "Hello",
        "channels": ["#test"],
    }

    with patch.object(bot, "_join_channels", new_callable=AsyncMock):
        await bot._handle_send_command(data)
        bot.connection.privmsg.assert_called_once_with("testuser", "Hello")


@pytest.mark.asyncio
async def test_handle_send_command_no_user(bot):
    """Test _handle_send_command with no user."""
    bot.connection = MagicMock()
    data = {
        "message": "Hello",
    }

    await bot._handle_send_command(data)
    bot.connection.privmsg.assert_not_called()


@pytest.mark.asyncio
async def test_handle_part_command(bot):
    """Test _handle_part_command."""
    bot.connection = MagicMock()
    bot.joined_channels["#test"] = 123456.0
    data = {
        "channels": ["#test"],
        "reason": "Goodbye",
    }

    await bot._handle_part_command(data)
    bot.connection.part.assert_called_once_with("#test", "Goodbye")


def test_update_channel_mapping(bot):
    """Test _update_channel_mapping."""
    bot._update_channel_mapping("testuser", ["#test1", "#test2"])
    assert "testuser" in bot.bot_channel_map
    assert bot.bot_channel_map["testuser"] == {"#test1", "#test2"}
    assert "#test1" in bot.joined_channels
    assert "#test2" in bot.joined_channels


def test_update_channel_mapping_existing_user(bot):
    """Test _update_channel_mapping with existing user."""
    bot.bot_channel_map["testuser"] = {"#test1"}
    bot._update_channel_mapping("testuser", ["#test2"])
    assert bot.bot_channel_map["testuser"] == {"#test1", "#test2"}


def test_on_ctcp_non_dcc(bot):
    """Test on_ctcp with non-DCC message."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.arguments = ["PING"]

    with patch.object(bot, "on_privmsg") as mock_privmsg:
        bot.on_ctcp(bot.connection, event)
        mock_privmsg.assert_called_once()


def test_on_ctcp_invalid(bot):
    """Test on_ctcp with invalid DCC message."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.arguments = ["DCC"]

    bot.on_ctcp(bot.connection, event)
    # Should not crash


def test_on_dcc_send_invalid_arguments(bot):
    """Test on_dcc_send with invalid arguments."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.arguments = ["DCC", "SEND"]  # Not enough arguments

    bot.on_dcc_send(bot.connection, event, False)
    # Should not crash


def test_on_dcc_send_invalid_filename(bot):
    """Test on_dcc_send with invalid filename."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SEND "../bad.txt" 127.0.0.1 5000 1000']

    bot.on_dcc_send(bot.connection, event, False)
    # Should reject the file


def test_on_dcc_send_file_too_large(bot):
    """Test on_dcc_send with file too large."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SEND "test.txt" 127.0.0.1 5000 10000000']  # 10MB, limit is 1MB

    bot.on_dcc_send(bot.connection, event, False)
    # Should reject the file


def test_on_dcc_send_private_ip_rejected(bot):
    """Test on_dcc_send with private IP address."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SEND "test.txt" 192.168.1.1 5000 1000']

    bot.on_dcc_send(bot.connection, event, False)
    # Should reject private IP


def test_on_dcc_send_private_ip_allowed(bot_factory, mock_bot_manager):
    """Test on_dcc_send with private IP when allowed."""
    mock_bot_manager.config = {"allow_private_ips": True}
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager, server_config={"channels": []})
    bot.connection = MagicMock()
    bot.mime_checker = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SEND "test.txt" 192.168.1.1 5000 1000']

    with patch.object(bot, "init_dcc_connection"):
        bot.on_dcc_send(bot.connection, event, False)
        # Should not reject


@pytest.mark.asyncio
async def test_join_channels(bot):
    """Test _join_channels method."""
    bot.connection = MagicMock()

    with patch.object(bot, "join_channel", new_callable=AsyncMock) as mock_join:
        bot.joined_channels["#test1"] = 123456.0
        await bot._join_channels(["#test1", "#test2"])
        assert mock_join.call_count == 2


@pytest.mark.asyncio
async def test_join_channels_with_also_join(bot_factory, mock_bot_manager):
    """Test _join_channels with also_join configuration."""
    bot = bot_factory(server_config={"also_join": {"#test": ["#extra1", "#extra2"]}}, allowed_mimetypes=None, manager=mock_bot_manager)
    bot.connection = MagicMock()

    with patch.object(bot, "join_channel", new_callable=AsyncMock) as mock_join:
        bot.joined_channels["#test"] = 123456.0
        bot.joined_channels["#extra1"] = 123456.0
        bot.joined_channels["#extra2"] = 123456.0
        await bot._join_channels(["#test"])
        # Should join #test, #extra1, and #extra2
        assert mock_join.call_count == 3


@pytest.mark.asyncio
async def test_handle_authentication_no_password(bot):
    """Test _handle_authentication without password."""
    await bot._handle_authentication()
    # Should complete immediately


@pytest.mark.asyncio
async def test_handle_authentication_with_password(bot_factory, mock_bot_manager):
    """Test _handle_authentication with password."""
    bot = bot_factory(server_config={"nickserv_password": "secret"}, allowed_mimetypes=None, manager=mock_bot_manager)

    # Set authenticated event immediately to avoid timeout
    bot.authenticated_event.set()
    await bot._handle_authentication()


@pytest.mark.asyncio
async def test_handle_authentication_timeout(bot_factory, mock_bot_manager):
    """Test _handle_authentication with timeout."""
    bot = bot_factory(server_config={"nickserv_password": "secret"}, allowed_mimetypes=None, manager=mock_bot_manager)

    # Don't set authenticated event, should timeout
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        await bot._handle_authentication()
        # Should handle timeout gracefully
