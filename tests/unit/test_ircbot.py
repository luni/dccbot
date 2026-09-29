"""Tests for IRCBot class."""

import asyncio
import contextlib
import os
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from dccbot.ircbot import IRCBot
from dccbot.transfers import create_pending_transfer


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


def test_ircbot_server_lowercase(mock_bot_manager, loop_patch):
    """Test IRCBot stores server address in lowercase."""
    bot = IRCBot(
        server="IRC.Example.COM",
        server_config={"nick": "testbot"},
        download_path="/tmp/downloads",
        allowed_mimetypes=["application/x-bittorrent"],
        max_file_size=1_000_000,
        bot_manager=mock_bot_manager,
    )
    assert bot.server == "irc.example.com"


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


@pytest.mark.asyncio
async def test_on_welcome(bot):
    """Test on_welcome handler."""
    bot.connection = MagicMock()
    event = MagicMock()

    with patch.object(bot, "process_command_queue", new_callable=AsyncMock) as mock_process:
        bot.on_welcome(bot.connection, event)
        await asyncio.sleep(0)
        mock_process.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_welcome_with_nickserv(bot_factory, mock_bot_manager):
    """Test on_welcome with NickServ authentication."""
    bot = bot_factory(server_config={"nickserv_password": "secret"}, allowed_mimetypes=None)
    bot.connection = MagicMock()
    event = MagicMock()

    with patch.object(bot, "process_command_queue", new_callable=AsyncMock) as mock_process:
        bot.on_welcome(bot.connection, event)
        await asyncio.sleep(0)
        bot.connection.privmsg.assert_called_once_with("NickServ", "IDENTIFY secret")
        mock_process.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_command_queue_handles_join(bot):
    """Test process_command_queue processes a join command."""
    bot.authenticated = True
    bot.authenticated_event.set()
    bot.server_config["channels"] = []

    with (
        patch.object(bot, "_handle_authentication"),
        patch.object(bot, "join_channel", new_callable=AsyncMock),
        patch.object(bot, "_join_channels", new_callable=AsyncMock) as mock_join,
    ):
        bot.command_queue.put_nowait({"command": "join", "channels": ["#test"]})
        try:
            await asyncio.wait_for(bot.process_command_queue(), timeout=0.5)
        except asyncio.TimeoutError:
            pass

    mock_join.assert_awaited_once_with(["#test"])


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


def test_on_privmsg_transfer_completed_normalizes_sparse_transfer(bot, mock_bot_manager):
    """MD5 completion notices should work even with sparse transfer records."""
    bot.connection = MagicMock()
    now = 1_700_000_000.0
    mock_bot_manager.transfers = {
        "movie.mkv": [
            {
                "server": "irc.example.com",
                "nick": "sender",
                "completed": now - 1,
            }
        ]
    }
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["** Transfer Completed movie.mkv md5sum: 0123456789abcdef0123456789abcdef"]

    with patch("time.time", return_value=now):
        bot.on_privmsg(bot.connection, event)

    transfer = mock_bot_manager.transfers["movie.mkv"][0]
    assert transfer["md5"] == "0123456789abcdef0123456789abcdef"
    assert "id" in transfer
    assert transfer["filename"] == "movie.mkv"


def test_on_privmsg_transfer_completed_accepts_uppercase_md5(bot, mock_bot_manager):
    """MD5 completion notices should accept uppercase hex and store lowercase."""
    bot.connection = MagicMock()
    now = 1_700_000_000.0
    mock_bot_manager.transfers = {
        "movie.mkv": [
            {
                "server": "irc.example.com",
                "nick": "sender",
                "completed": now - 1,
            }
        ]
    }
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["** Transfer Completed movie.mkv MD5sum: ABCDEF0123456789ABCDEF0123456789"]

    with patch("time.time", return_value=now):
        bot.on_privmsg(bot.connection, event)

    transfer = mock_bot_manager.transfers["movie.mkv"][0]
    assert transfer["md5"] == "abcdef0123456789abcdef0123456789"


def test_on_privmsg_sending_pack_creates_normalized_pending_transfer(bot, mock_bot_manager):
    """Pack announcement should create a normalized pending transfer record."""
    bot.connection = MagicMock()
    mock_bot_manager.transfers = {}
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ['** Sending you pack #1 ("TEST.mkv") [1.0GB, MD5:82ce0f4fe6e5c862d54dae475b8a1b82] - (resume+ssl supported)']

    bot.on_privmsg(bot.connection, event)

    transfer = mock_bot_manager.transfers["TEST.mkv"][0]
    assert transfer["filename"] == "TEST.mkv"
    assert transfer["status"] == "started"
    assert transfer["size"] == 0
    assert transfer["peer_address"] is None
    assert transfer["md5"] == "82ce0f4fe6e5c862d54dae475b8a1b82"


def test_on_privmsg_sending_pack_multi_digit(bot, mock_bot_manager):
    """Pack announcement should match multi-digit pack numbers."""
    bot.connection = MagicMock()
    mock_bot_manager.transfers = {}
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ['** Sending you pack #42 ("TEST.mkv") [1.0GB, MD5:82ce0f4fe6e5c862d54dae475b8a1b82] - (resume+ssl supported)']

    bot.on_privmsg(bot.connection, event)

    assert "TEST.mkv" in mock_bot_manager.transfers


def test_on_privmsg_sending_pack_accepts_uppercase_md5(bot, mock_bot_manager):
    """Pack announcement should accept uppercase MD5 and store it lowercase."""
    bot.connection = MagicMock()
    mock_bot_manager.transfers = {}
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ['** Sending you pack #1 ("TEST.mkv") [1.0GB, MD5:ABCDEF0123456789ABCDEF0123456789] - (resume+ssl supported)']

    bot.on_privmsg(bot.connection, event)

    transfer = mock_bot_manager.transfers["TEST.mkv"][0]
    assert transfer["md5"] == "abcdef0123456789abcdef0123456789"


def test_on_privmsg_stores_nick_lowercase(bot, mock_bot_manager):
    """Pack announcement should store the sender nick in lowercase."""
    bot.connection = MagicMock()
    mock_bot_manager.transfers = {}
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "SeNdEr"
    event.arguments = ['** Sending you pack #1 ("TEST.mkv") [1.0GB, MD5:82ce0f4fe6e5c862d54dae475b8a1b82] - (resume+ssl supported)']

    bot.on_privmsg(bot.connection, event)

    transfer = mock_bot_manager.transfers["TEST.mkv"][0]
    assert transfer["nick"] == "sender"


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


def test_on_join_mixed_case_nick(bot):
    """Test on_join recognizes the bot despite case differences in nick."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "TestBot"
    event.target = "#test"
    event.arguments = []

    bot.on_join(bot.connection, event)
    assert "#test" in bot.joined_channels


def test_on_part_mixed_case_nick(bot):
    """Test on_part recognizes the bot despite case differences in nick."""
    bot.connection = MagicMock()
    bot.joined_channels["#test"] = 123456.0
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "TestBot"
    event.target = "#test"
    event.arguments = []

    bot.on_part(bot.connection, event)
    assert "#test" not in bot.joined_channels


def test_on_kick_other_user_ignored(bot):
    """Test on_kick does not remove the channel when another user is kicked."""
    bot.connection = MagicMock()
    bot.joined_channels["#test"] = 123456.0
    event = MagicMock()
    event.target = "#test"
    event.arguments = ["otheruser", "reason"]

    bot.on_kick(bot.connection, event)
    assert "#test" in bot.joined_channels


def test_resolve_channel_from_event_fallback_priority(bot):
    """Ensure fallback is preferred when resolving channel names."""
    event = MagicMock()
    event.arguments = ["#from_args"]
    event.target = "#from_target"

    result = bot._resolve_channel_from_event(event, fallback="#from_fallback")

    assert result == "#from_fallback"


def test_resolve_channel_from_event_uses_arguments(bot):
    """Ensure arguments are used when fallback is missing."""
    event = MagicMock()
    event.arguments = ["#from_args", bot.nick]
    event.target = bot.nick

    result = bot._resolve_channel_from_event(event)

    assert result == "#from_args"


def test_store_join_failure_records_reason(bot):
    """Verify join failures are tracked and remove pending channel state."""
    event = MagicMock()
    event.arguments = ["#chan"]
    bot.joined_channels["#chan"] = 123456.0

    bot._store_join_failure(event, "Test reason")

    assert bot.pending_join_failures["#chan"] == "Test reason"
    assert "#chan" not in bot.joined_channels


@pytest.mark.parametrize(
    ("handler_name", "expected_reason"),
    [
        ("on_channelisfull", "Channel is full"),
        ("on_inviteonlychan", "Invite-only channel"),
        ("on_badchannelkey", "Bad channel key/password"),
        ("on_badchanmask", "Bad channel mask"),
        ("on_toomanychannels", "Too many channels joined"),
    ],
)
def test_join_failure_numerics_call_store(bot, handler_name, expected_reason):
    """Static reason numerics should forward the expected reason to the helper."""
    event = MagicMock()
    event.arguments = ["#chan", "details"]

    with patch.object(bot, "_store_join_failure") as mock_store:
        getattr(bot, handler_name)(bot.connection, event)

    mock_store.assert_called_once_with(event, expected_reason)


def test_on_nochanmodes_uses_server_reason(bot):
    """ERR_NOCHANMODES should pass through server supplied text."""
    event = MagicMock()
    event.arguments = ["#chan", "mode restriction"]

    with patch.object(bot, "_store_join_failure") as mock_store:
        bot.on_nochanmodes(bot.connection, event)

    mock_store.assert_called_once_with(event, "mode restriction")


def test_on_nosuchchannel_uses_second_argument_when_available(bot):
    """ERR_NOSUCHCHANNEL should prefer human readable server reason."""
    event = MagicMock()
    event.arguments = ["#chan", "No such channel"]

    with patch.object(bot, "_store_join_failure") as mock_store:
        bot.on_nosuchchannel(bot.connection, event)

    mock_store.assert_called_once_with(event, "No such channel")


def test_on_bannedfromchan_tracks_banned_channels(bot):
    """ERR_BANNEDFROMCHAN should mark the channel as banned."""
    event = MagicMock()
    event.arguments = ["#chan"]

    with patch.object(bot, "_store_join_failure") as mock_store:
        bot.on_bannedfromchan(bot.connection, event)

    mock_store.assert_called_once_with(event, "Banned from channel")
    assert "#chan" in bot.banned_channels


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
    """Test _update_channel_mapping only touches channels the bot has joined."""
    bot.joined_channels = {"#test1": 1.0, "#test2": 1.0}
    bot._update_channel_mapping("testuser", ["#test1", "#test2"])
    assert "testuser" in bot.bot_channel_map
    assert bot.bot_channel_map["testuser"] == {"#test1", "#test2"}
    assert bot.joined_channels["#test1"] > 1.0
    assert bot.joined_channels["#test2"] > 1.0


def test_update_channel_mapping_does_not_create_phantom_channels(bot):
    """Test _update_channel_mapping does not add unjoined channels."""
    bot.joined_channels = {"#test1": 1.0}
    bot._update_channel_mapping("testuser", ["#test1", "#test2"])
    assert bot.bot_channel_map["testuser"] == {"#test1", "#test2"}
    assert "#test2" not in bot.joined_channels


def test_update_channel_mapping_existing_user(bot):
    """Test _update_channel_mapping with existing user."""
    bot.joined_channels = {"#test1": 1.0, "#test2": 1.0}
    bot.bot_channel_map["testuser"] = {"#test1"}
    bot._update_channel_mapping("testuser", ["#test2"])
    assert bot.bot_channel_map["testuser"] == {"#test1", "#test2"}
    assert bot.joined_channels["#test2"] > 1.0


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


@pytest.mark.asyncio
async def test_add_md5_check_queue_item(bot):
    """Test helper that enqueues transfer for md5 verification."""
    bot.bot_manager.md5_check_queue = asyncio.Queue()
    transfer = {"filename": "file.bin"}
    await bot._add_md5_check_queue_item(transfer)
    queued = await bot.bot_manager.md5_check_queue.get()
    assert queued == transfer


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


def test_on_dccmsg_delegates_to_transfer_handler(bot):
    """Test on_dccmsg delegates to TransferHandler."""
    bot.transfer_handler = MagicMock()
    connection = MagicMock()
    event = MagicMock()
    bot.on_dccmsg(connection, event)
    bot.transfer_handler.on_dccmsg.assert_called_once_with(connection, event)


def test_on_dcc_disconnect_delegates_to_transfer_handler(bot):
    """Test on_dcc_disconnect delegates to TransferHandler."""
    bot.transfer_handler = MagicMock()
    connection = MagicMock()
    event = MagicMock()
    bot.on_dcc_disconnect(connection, event)
    bot.transfer_handler.on_dcc_disconnect.assert_called_once_with(connection, event)


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


def test_on_dcc_send_resume_small_completed_file(bot_factory, mock_bot_manager, tmp_path):
    """Test on_dcc_send resumes from 0 for completed files smaller than 4096 bytes."""
    mock_bot_manager.config = {"allow_private_ips": True}
    bot = bot_factory(
        allowed_mimetypes=None,
        manager=mock_bot_manager,
        server_config={"channels": []},
        download_path=str(tmp_path),
    )
    bot.connection = MagicMock()
    bot.mime_checker = MagicMock()
    (tmp_path / "test.txt").write_bytes(b"x" * 100)

    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    # 127.0.0.1 as 32-bit integer, port 5000, size 100
    event.arguments = ["DCC", 'SEND "test.txt" 2130706433 5000 100']

    bot.on_dcc_send(bot.connection, event, False)

    ctcp_call = bot.connection.ctcp_reply.call_args[0]
    assert "RESUME" in ctcp_call[1]
    assert "5000" in ctcp_call[1]
    assert ctcp_call[1].endswith(" 0")


def test_on_ctcp_with_missing_arguments(bot):
    """Test on_ctcp with malformed/short argument list."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.arguments = []

    # Should not raise
    bot.on_ctcp(bot.connection, event)


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

    # Don't set authenticated event, should timeout. Use a short real wait_for
    # so the event's wait() coroutine is scheduled and cancelled instead of discarded.
    real_wait_for = asyncio.wait_for

    async def fake_wait_for(coro, timeout):
        await real_wait_for(coro, 0.01)

    with patch("asyncio.wait_for", new=fake_wait_for):
        await bot._handle_authentication()
        # Should handle timeout gracefully


def test_get_passive_dcc_config_disabled(bot):
    """Test passive DCC config defaults to disabled."""
    enabled, listen_ip, port_range = bot._get_passive_dcc_config()
    assert enabled is False
    assert listen_ip is None
    assert port_range is None


def test_get_passive_dcc_config_global(bot_factory, mock_bot_manager):
    """Test passive DCC config from global config."""
    mock_bot_manager.config = {
        "passive_dcc": True,
        "passive_dcc_listen_ip": "192.168.1.10",
        "passive_dcc_port_range": [10000, 20000],
    }
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager)
    enabled, listen_ip, port_range = bot._get_passive_dcc_config()
    assert enabled is True
    assert listen_ip == "192.168.1.10"
    assert port_range == (10000, 20000)


def test_get_passive_dcc_config_unset_wildcard_listen_ip(bot_factory, mock_bot_manager):
    """Test 0.0.0.0 / :: are treated as unset listen IPs."""
    mock_bot_manager.config = {
        "passive_dcc": True,
        "passive_dcc_listen_ip": "0.0.0.0",
        "passive_dcc_port_range": [10000, 20000],
    }
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager)
    enabled, listen_ip, port_range = bot._get_passive_dcc_config()
    assert enabled is True
    assert listen_ip is None
    assert port_range == (10000, 20000)


def test_get_passive_dcc_config_server_override(bot_factory, mock_bot_manager):
    """Test per-server config overrides global config."""
    mock_bot_manager.config = {
        "passive_dcc": True,
        "passive_dcc_listen_ip": "192.168.1.10",
        "passive_dcc_port_range": [10000, 20000],
    }
    bot = bot_factory(
        allowed_mimetypes=None,
        manager=mock_bot_manager,
        server_config={
            "passive_dcc": False,
            "passive_dcc_listen_ip": "127.0.0.1",
            "passive_dcc_port_range": [30000, 40000],
        },
    )
    enabled, listen_ip, port_range = bot._get_passive_dcc_config()
    assert enabled is False
    assert listen_ip == "127.0.0.1"
    assert port_range == (30000, 40000)


def test_get_passive_dcc_config_invalid_port_range(bot_factory, mock_bot_manager):
    """Test invalid port ranges fall back to None."""
    mock_bot_manager.config = {
        "passive_dcc": True,
        "passive_dcc_port_range": [70000, 80000],
    }
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager)
    enabled, listen_ip, port_range = bot._get_passive_dcc_config()
    assert enabled is True
    assert port_range is None


def test_on_dcc_send_passive_disabled(bot):
    """Test on_dcc_send rejects passive DCC when not enabled."""
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SEND "test.txt" 0 0 1000 42']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, False)
        mock_init.assert_not_called()


def test_on_dcc_send_passive_enabled(bot_factory, mock_bot_manager):
    """Test on_dcc_send initiates passive DCC when enabled."""
    mock_bot_manager.config = {"passive_dcc": True}
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager)
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SEND "test.txt" 0 0 1000 42']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, False)
        mock_init.assert_called_once_with("sender", "test.txt", 1000, None, None, token=42, use_ssl=False)


def test_on_dcc_send_passive_lower_cases_nick(bot_factory, mock_bot_manager):
    """Test on_dcc_send passes a lowercased nick to passive DCC init."""
    mock_bot_manager.config = {"passive_dcc": True}
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager)
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "SeNdEr"
    event.arguments = ["DCC", 'SEND "test.txt" 0 0 1000 42']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, False)
        mock_init.assert_called_once_with("sender", "test.txt", 1000, None, None, token=42, use_ssl=False)


def test_on_dcc_send_passive_missing_token(bot_factory, mock_bot_manager):
    """Test on_dcc_send rejects passive DCC offer without a token."""
    mock_bot_manager.config = {"passive_dcc": True}
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager)
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SEND "test.txt" 0 0 1000']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, False)
        mock_init.assert_not_called()


def test_on_dcc_send_passive_enabled_invalid_filename(bot_factory, mock_bot_manager):
    """Test on_dcc_send rejects passive DCC with invalid filename."""
    mock_bot_manager.config = {"passive_dcc": True}
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager)
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SEND "" 0 0 1000 42']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, False)
        mock_init.assert_not_called()


def test_on_dcc_send_does_not_reject_transfer_from_other_server(bot, mock_bot_manager):
    """Test on_dcc_send allows same nick/file on a different server."""
    bot.config["allow_private_ips"] = True
    bot.bot_manager.transfers = {
        "test.txt": [
            {
                "size": 1000,
                "connected": True,
                "nick": "sender",
                "server": "other.server",
            }
        ]
    }
    bot.connection = MagicMock()
    bot.mime_checker = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SEND "test.txt" 2130706433 5000 1000']

    with patch.object(bot, "init_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, False)

    mock_init.assert_called_once()


def test_on_dcc_send_passive_skips_existing_complete_file(bot_factory, mock_bot_manager, tmp_path):
    """Test passive DCC ignores the request when the file is already complete."""
    mock_bot_manager.config = {"passive_dcc": True, "incomplete_suffix": ".incomplete"}
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager, download_path=str(tmp_path))
    bot.connection = MagicMock()
    (tmp_path / "test.txt").write_bytes(b"x" * 1000)

    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SEND "test.txt" 0 0 1000 42']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, False)

    mock_init.assert_not_called()


def test_on_dcc_send_passive_resumes_partial_existing_file(bot_factory, mock_bot_manager, tmp_path):
    """Test passive DCC sends RESUME for a partial file and stores a resume entry."""
    mock_bot_manager.config = {"passive_dcc": True}
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager, download_path=str(tmp_path))
    bot.connection = MagicMock()
    (tmp_path / "test.txt").write_bytes(b"x" * 500)

    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SEND "test.txt" 0 0 1000 42']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, False)

    mock_init.assert_not_called()
    assert ("sender", 42) in bot.passive_resume_queue
    bot.connection.ctcp_reply.assert_called_once()
    reply = bot.connection.ctcp_reply.call_args[0][1]
    assert "RESUME" in reply
    assert "0" in reply
    assert "500" in reply
    assert "42" in reply


def test_on_dcc_send_passive_enabled_invalid_size(bot_factory, mock_bot_manager):
    """Test on_dcc_send rejects passive DCC with invalid size (0 and oversized)."""
    mock_bot_manager.config = {"passive_dcc": True}
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager)
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"

    # Zero size
    event.arguments = ["DCC", 'SEND "test.txt" 0 0 0 42']
    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, False)
        mock_init.assert_not_called()

    # Oversized
    event.arguments = ["DCC", 'SEND "test.txt" 0 0 9999999999 42']
    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, False)
        mock_init.assert_not_called()


@pytest.mark.asyncio
async def test_init_passive_dcc_connection(bot):
    """Test passive DCC connection setup with token."""
    bot.connection = MagicMock()
    bot.server_config["passive_dcc_timeout"] = 0
    mock_dcc = MagicMock()
    mock_dcc.localaddress = "192.168.1.100"
    mock_dcc.localport = 12345

    mock_listen = AsyncMock(return_value=mock_dcc)
    mock_dcc.listen = mock_listen

    with patch.object(bot, "dcc", return_value=mock_dcc) as mock_dcc_factory:
        with patch.object(bot.loop, "create_task") as mock_create_task:
            bot.init_passive_dcc_connection("sender", "test.txt", 1000, "192.168.1.100", (10000, 20000), token=42)
            mock_dcc_factory.assert_called_once_with("raw")
            # Verify the setup task was scheduled
            mock_create_task.assert_called_once()
            # Await the inner coroutine directly to verify behavior
            coro = mock_create_task.call_args[0][0]
            await coro

    mock_listen.assert_called_once_with(addr="192.168.1.100", port=(10000, 20000), ssl=None)
    bot.connection.ctcp_reply.assert_called_once()
    reply = bot.connection.ctcp_reply.call_args[0][1]
    assert reply.startswith("DCC SEND")
    assert "42" in reply
    assert len(bot.current_transfers) == 1
    transfer = list(bot.current_transfers.values())[0]
    assert transfer["filename"] == "test.txt"
    assert transfer["nick"] == "sender"
    assert transfer["size"] == 1000


def test_on_dcc_connect(bot):
    """Test on_dcc_connect handler."""
    event = MagicMock()
    event.source = "192.168.1.1"
    # Should not raise
    bot.on_dcc_connect(MagicMock(), event)


def test_init_dcc_connection_preserves_pending_md5(bot, mock_bot_manager):
    """Test active DCC init preserves MD5 from pack announcement."""
    bot.connection = MagicMock()
    bot.bot_manager = mock_bot_manager
    pending = create_pending_transfer("test.txt", "sender", "irc.example.com", md5="abc123", now=time.time())
    mock_bot_manager.transfers = {"test.txt": [pending]}

    with patch.object(bot, "loop") as mock_loop, patch.object(bot, "dcc", return_value=MagicMock()):
        bot.init_dcc_connection("sender", "127.0.0.1", 5000, "test.txt", "/tmp/downloads/test.txt", 1024, 0, False, False)

    assert pending["md5"] == "abc123"
    assert pending["id"]
    mock_loop.create_task.assert_called_once()


def test_init_dcc_connection_creates_download_directory(bot, mock_bot_manager, tmp_path):
    """Test active DCC init creates the download directory if missing."""
    bot.connection = MagicMock()
    bot.bot_manager = mock_bot_manager
    bot.download_path = str(tmp_path / "missing")

    with patch.object(bot, "loop"), patch.object(bot, "dcc", return_value=MagicMock()):
        bot.init_dcc_connection("sender", "127.0.0.1", 5000, "test.txt", str(tmp_path / "missing" / "test.txt"), 1024, 0, False, False)

    assert (tmp_path / "missing").is_dir()


def test_on_dcc_accept_active_resume(bot):
    """Test on_dcc_accept triggers an active DCC resume."""
    bot.connection = MagicMock()
    bot.resume_queue["sender"] = [
        ("127.0.0.1", 5000, "test.txt", "/tmp/downloads/test.txt", 1000, 500, False, False, time.time()),
    ]

    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'ACCEPT "test.txt" 5000 500']

    with patch.object(bot, "init_dcc_connection") as mock_init:
        bot.on_dcc_accept(bot.connection, event)

    mock_init.assert_called_once_with("sender", "127.0.0.1", 5000, "test.txt", "/tmp/downloads/test.txt", 1000, 500, False, False)
    assert "sender" not in bot.resume_queue


def test_on_dcc_accept_passive_resume(bot):
    """Test on_dcc_accept triggers a passive DCC resume from the queue."""
    bot.connection = MagicMock()
    bot.server_config["passive_dcc_timeout"] = 0
    bot.passive_resume_queue[("sender", 42)] = {
        "filename": "test.txt",
        "size": 1000,
        "offset": 500,
        "file_path": "/tmp/downloads/test.txt",
        "listen_ip": "127.0.0.1",
        "port_range": (15000, 16000),
        "requested_time": time.time(),
    }

    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'ACCEPT "test.txt" 0 500 42']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_accept(bot.connection, event)

    mock_init.assert_called_once_with(
        "sender",
        "test.txt",
        1000,
        "127.0.0.1",
        (15000, 16000),
        token=42,
        use_ssl=False,
        offset=500,
        file_path="/tmp/downloads/test.txt",
    )
    assert ("sender", 42) not in bot.passive_resume_queue


def test_on_dcc_send_passive_ssend(bot_factory, mock_bot_manager):
    """Test on_dcc_send initiates a passive SDCC connection when SSEND is offered."""
    mock_bot_manager.config = {"passive_dcc": True}
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager)
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SSEND "test.txt" 0 0 1000 42']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, True)
        mock_init.assert_called_once_with("sender", "test.txt", 1000, None, None, token=42, use_ssl=True)


def test_on_dcc_send_active_ssend(bot, mock_bot_manager):
    """Test on_dcc_send initiates an active SDCC connection for SSEND."""
    bot.config["allow_private_ips"] = True
    bot.bot_manager.transfers = {}
    bot.connection = MagicMock()
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SSEND "test.txt" 2130706433 5000 1000']

    with patch.object(bot, "init_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, True)

    mock_init.assert_called_once_with("sender", "127.0.0.1", 5000, "test.txt", "/tmp/downloads/test.txt", 1000, 0, True, False)


@pytest.mark.asyncio
async def test_init_passive_dcc_connection_ssend(bot):
    """Test passive SDCC listener is set up and a SSEND reverse CTCP is sent."""
    bot.connection = MagicMock()
    bot.server_config["passive_dcc_timeout"] = 0
    mock_ssl_ctx = MagicMock()
    with patch.object(bot, "_get_dcc_ssl_context", return_value=mock_ssl_ctx):
        mock_dcc = MagicMock()
        mock_dcc.localaddress = "192.168.1.100"
        mock_dcc.localport = 12345

        mock_listen = AsyncMock(return_value=mock_dcc)
        mock_dcc.listen = mock_listen

        with patch.object(bot, "dcc", return_value=mock_dcc) as mock_dcc_factory:
            with patch.object(bot.loop, "create_task") as mock_create_task:
                bot.init_passive_dcc_connection("sender", "test.txt", 1000, "192.168.1.100", (10000, 20000), token=42, use_ssl=True)
                mock_dcc_factory.assert_called_once_with("raw")
                mock_create_task.assert_called_once()
                coro = mock_create_task.call_args[0][0]
                await coro

    mock_listen.assert_called_once_with(addr="192.168.1.100", port=(10000, 20000), ssl=mock_ssl_ctx)
    bot.connection.ctcp_reply.assert_called_once()
    reply = bot.connection.ctcp_reply.call_args[0][1]
    assert reply.startswith("DCC SSEND")
    assert "42" in reply

    transfer = list(bot.current_transfers.values())[0]
    assert transfer["ssl"] is True


def test_on_dcc_accept_passive_ssend_resume(bot):
    """Test on_dcc_accept forwards use_ssl for a passive SSEND resume."""
    bot.connection = MagicMock()
    bot.server_config["passive_dcc_timeout"] = 0
    bot.passive_resume_queue[("sender", 42)] = {
        "filename": "test.txt",
        "size": 1000,
        "offset": 500,
        "file_path": "/tmp/downloads/test.txt",
        "listen_ip": "127.0.0.1",
        "port_range": (15000, 16000),
        "use_ssl": True,
        "requested_time": time.time(),
    }

    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'ACCEPT "test.txt" 0 500 42']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_accept(bot.connection, event)

    mock_init.assert_called_once_with(
        "sender",
        "test.txt",
        1000,
        "127.0.0.1",
        (15000, 16000),
        token=42,
        use_ssl=True,
        offset=500,
        file_path="/tmp/downloads/test.txt",
    )


def test_on_dcc_send_passive_ssend_partial_resume(bot_factory, mock_bot_manager, tmp_path):
    """Test passive SSEND with a partial file sends RESUME and stores use_ssl."""
    mock_bot_manager.config = {"passive_dcc": True, "incomplete_suffix": ".incomplete"}
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager, download_path=str(tmp_path))
    bot.connection = MagicMock()
    (tmp_path / "test.txt.incomplete").write_bytes(b"x" * 500)

    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SSEND "test.txt" 0 0 1000 42']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, True)

    mock_init.assert_not_called()
    assert ("sender", 42) in bot.passive_resume_queue
    assert bot.passive_resume_queue[("sender", 42)]["use_ssl"] is True
    reply = bot.connection.ctcp_reply.call_args[0][1]
    assert "RESUME" in reply


def test_on_dcc_send_passive_ssend_oversized_local_file(bot_factory, mock_bot_manager, tmp_path):
    """Test passive SSEND is rejected when a local file exceeds the remote size."""
    mock_bot_manager.config = {"passive_dcc": True}
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager, download_path=str(tmp_path))
    bot.connection = MagicMock()
    (tmp_path / "test.txt").write_bytes(b"x" * 2000)

    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SSEND "test.txt" 0 0 1000 42']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, True)

    mock_init.assert_not_called()
    assert bot.connection.ctcp_reply.called is False


def test_on_dcc_send_passive_ssend_missing_token(bot_factory, mock_bot_manager):
    """Test passive SSEND without a token is rejected."""
    mock_bot_manager.config = {"passive_dcc": True}
    bot = bot_factory(allowed_mimetypes=None, manager=mock_bot_manager)
    bot.connection = MagicMock()

    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SSEND "test.txt" 0 0 1000']

    with patch.object(bot, "init_passive_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, True)

    mock_init.assert_not_called()


def test_on_dcc_send_active_ssend_complete_local_file(bot, mock_bot_manager, tmp_path):
    """Test active SSEND with a complete local file sends RESUME from size-4096."""
    bot.config["allow_private_ips"] = True
    bot.bot_manager.transfers = {}
    bot.download_path = str(tmp_path)
    bot.connection = MagicMock()
    (tmp_path / "test.txt").write_bytes(b"x" * 1000)

    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SSEND "test.txt" 2130706433 5000 1000']

    with patch.object(bot, "init_dcc_connection") as mock_init:
        bot.on_dcc_send(bot.connection, event, True)

    # It should not start a new transfer for an already-complete file.
    mock_init.assert_not_called()
    assert "sender" in bot.resume_queue
    assert bot.resume_queue["sender"][0][6] is True  # use_ssl
    assert bot.resume_queue["sender"][0][7] is True  # completed


@pytest.mark.asyncio
async def test_init_dcc_connection_ssend(bot, mock_bot_manager):
    """Test active SSEND uses an SSL context and the transfer records ssl=True."""
    bot.connection = MagicMock()
    bot.bot_manager = mock_bot_manager
    mock_ssl_ctx = MagicMock()

    mock_dcc = MagicMock()
    mock_dcc.connect = AsyncMock(return_value=MagicMock())

    with patch.object(bot, "_get_dcc_ssl_context", return_value=mock_ssl_ctx):
        with patch.object(bot, "dcc", return_value=mock_dcc) as mock_dcc_factory:
            with patch.object(bot.loop, "create_task") as mock_create_task:
                bot.init_dcc_connection("sender", "127.0.0.1", 5000, "test.txt", "/tmp/downloads/test.txt", 1024, 0, True, False)

                mock_dcc_factory.assert_called_once_with("raw")
                mock_create_task.assert_called_once()
                coro = mock_create_task.call_args[0][0]
                await coro

    # The connect call should have been made with an AioFactory carrying the SSL context.
    mock_dcc.connect.assert_called_once()
    factory = mock_dcc.connect.call_args.kwargs["connect_factory"]
    assert factory.connection_args["ssl"] is mock_ssl_ctx

    transfer = list(bot.current_transfers.values())[0]
    assert transfer["ssl"] is True


def test_get_send_queue_setting_uses_default(bot):
    """Test _get_send_queue_setting falls back to the given default."""
    assert bot._get_send_queue_setting("send_queue_delay", 5) == 5


def test_get_send_queue_setting_global_override(bot_factory, mock_bot_manager):
    """Test _get_send_queue_setting reads the global config value."""
    mock_bot_manager.config = {"send_queue_delay": 10}
    bot = bot_factory(manager=mock_bot_manager)
    assert bot._get_send_queue_setting("send_queue_delay", 5) == 10


def test_get_send_queue_setting_server_override(bot_factory, mock_bot_manager):
    """Test _get_send_queue_setting prefers the per-server value over global."""
    mock_bot_manager.config = {"send_queue_delay": 10}
    bot = bot_factory(server_config={"send_queue_delay": 1}, manager=mock_bot_manager)
    assert bot._get_send_queue_setting("send_queue_delay", 5) == 1


def test_get_send_queue_setting_invalid_value(bot):
    """Test _get_send_queue_setting falls back to the default on a bad value."""
    bot.config["send_queue_delay"] = "not-a-number"
    assert bot._get_send_queue_setting("send_queue_delay", 5) == 5


def test_get_send_queue_setting_rejects_non_finite_and_negative(bot):
    """Test _get_send_queue_setting falls back on nan/inf/negative values."""
    for bad in ("nan", "inf", "-inf", float("nan"), float("inf"), -1, -0.5):
        bot.server_config["send_queue_delay"] = bad
        assert bot._get_send_queue_setting("send_queue_delay", 5) == 5, bad


@pytest.mark.asyncio
async def test_process_queued_send_respects_manager_semaphore(bot, mock_bot_manager):
    """Test _process_queued_send serializes sends across bots through the manager semaphore."""
    mock_bot_manager.send_queue_semaphore = asyncio.Semaphore(1)
    order: list[str] = []

    async def slow_send(_bot, data):
        order.append(f"start {data['message']}")
        await asyncio.sleep(0.05)
        order.append(f"end {data['message']}")

    with (
        patch("dccbot.ircbot.handle_send_command", side_effect=slow_send),
        patch.object(bot, "_wait_for_queued_transfer", new_callable=AsyncMock),
    ):
        await asyncio.gather(
            bot._process_queued_send("botone", {"user": "botone", "message": "one"}),
            bot._process_queued_send("bottwo", {"user": "bottwo", "message": "two"}),
        )

    assert order == ["start one", "end one", "start two", "end two"]


@pytest.mark.asyncio
async def test_process_queued_send_without_semaphore(bot, mock_bot_manager):
    """Test _process_queued_send sends directly when no concurrency limit is set."""
    mock_bot_manager.send_queue_semaphore = None

    with (
        patch("dccbot.ircbot.handle_send_command", new_callable=AsyncMock) as mock_send,
        patch.object(bot, "_wait_for_queued_transfer", new_callable=AsyncMock),
    ):
        await bot._process_queued_send("mybot", {"user": "mybot", "message": "xdcc send #1"})

    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_queued_send_ignores_non_semaphore_attr(bot, mock_bot_manager):
    """Test a non-semaphore send_queue_semaphore attribute (e.g. plain mock) is ignored."""
    mock_bot_manager.send_queue_semaphore = MagicMock()

    with (
        patch("dccbot.ircbot.handle_send_command", new_callable=AsyncMock) as mock_send,
        patch.object(bot, "_wait_for_queued_transfer", new_callable=AsyncMock),
    ):
        await bot._process_queued_send("mybot", {"user": "mybot", "message": "xdcc send #1"})

    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_queue_send_requires_user_and_message(bot):
    """Test queue_send does nothing without a user and message."""
    await bot.queue_send({"message": "hi"})
    await bot.queue_send({"user": "MyBot"})
    assert bot.send_queues == {}
    assert bot.send_queue_tasks == {}


@pytest.mark.asyncio
async def test_queue_send_spawns_one_task_per_nick(bot):
    """Test queue_send lazily spawns a single consumer task per nick."""
    with patch("dccbot.ircbot.asyncio.create_task") as mock_create_task:
        mock_create_task.return_value = MagicMock()
        await bot.queue_send({"user": "MyBot", "message": "xdcc send #1"})
        await bot.queue_send({"user": "mybot", "message": "xdcc send #2"})
        # The consumer coroutine was handed to the mocked create_task instead
        # of actually being scheduled; close it to avoid an "never awaited" warning.
        mock_create_task.call_args[0][0].close()

    mock_create_task.assert_called_once()
    assert list(bot.send_queues.keys()) == ["mybot"]
    assert bot.send_queues["mybot"].qsize() == 2


def _queued_messages(bot, nick="mybot"):
    """Return the pending queued send messages for a nick."""
    return [item["message"] for item in bot.send_queue_items.get(nick, [])]


@pytest.mark.asyncio
async def test_queue_send_expands_numeric_range(bot):
    """Test queue_send expands a numeric send range into one send per pack."""
    with patch("dccbot.ircbot.asyncio.create_task") as mock_create_task:
        mock_create_task.return_value = MagicMock()
        await bot.queue_send({"user": "MyBot", "message": "xdcc send 1-3"})
        mock_create_task.call_args[0][0].close()

    assert _queued_messages(bot) == ["xdcc send #1", "xdcc send #2", "xdcc send #3"]
    assert bot.send_queues["mybot"].qsize() == 3


@pytest.mark.asyncio
async def test_queue_send_expands_batch_range_as_send(bot):
    """Test an xdcc batch range is expanded to single-pack sends (works on bots without batch support)."""
    with patch("dccbot.ircbot.asyncio.create_task") as mock_create_task:
        mock_create_task.return_value = MagicMock()
        await bot.queue_send({"user": "MyBot", "message": "xdcc batch 1-2"})
        mock_create_task.call_args[0][0].close()

    assert _queued_messages(bot) == ["xdcc send #1", "xdcc send #2"]


@pytest.mark.asyncio
async def test_queue_send_expands_descending_range_and_lists(bot):
    """Test descending ranges keep their order and comma segments mix."""
    with patch("dccbot.ircbot.asyncio.create_task") as mock_create_task:
        mock_create_task.return_value = MagicMock()
        await bot.queue_send({"user": "MyBot", "message": "xdcc batch 5-3"})
        await bot.queue_send({"user": "OtherBot", "message": "xdcc send 1,3-4"})
        mock_create_task.call_args[0][0].close()
        mock_create_task.call_args[0][0].close()

    assert _queued_messages(bot) == ["xdcc send #5", "xdcc send #4", "xdcc send #3"]
    assert _queued_messages(bot, "otherbot") == ["xdcc send #1", "xdcc send #3", "xdcc send #4"]


@pytest.mark.asyncio
async def test_queue_send_expanded_items_are_distinct(bot):
    """Test each expanded queue entry is its own dict so cancelling one doesn't flag the others."""
    with patch("dccbot.ircbot.asyncio.create_task") as mock_create_task:
        mock_create_task.return_value = MagicMock()
        await bot.queue_send({"user": "MyBot", "message": "xdcc send 1-2"})
        mock_create_task.call_args[0][0].close()

    items = bot.send_queue_items["mybot"]
    assert len(items) == 2
    assert items[0] is not items[1]
    items[0]["cancelled"] = True
    assert "cancelled" not in items[1]


@pytest.mark.asyncio
async def test_queue_send_range_preserves_ssend_verb(bot):
    """Test an SSL-rewritten ssend/sbatch range expands to ssend."""
    with patch("dccbot.ircbot.asyncio.create_task") as mock_create_task:
        mock_create_task.return_value = MagicMock()
        await bot.queue_send({"user": "MyBot", "message": "xdcc sbatch 1-2"})
        mock_create_task.call_args[0][0].close()

    assert _queued_messages(bot) == ["xdcc ssend #1", "xdcc ssend #2"]


@pytest.mark.asyncio
async def test_queue_send_range_preserves_password(bot):
    """Test a trailing password is appended to every expanded send."""
    with patch("dccbot.ircbot.asyncio.create_task") as mock_create_task:
        mock_create_task.return_value = MagicMock()
        await bot.queue_send({"user": "MyBot", "message": "xdcc batch 1-2 secretpw"})
        mock_create_task.call_args[0][0].close()

    assert _queued_messages(bot) == ["xdcc send #1 secretpw", "xdcc send #2 secretpw"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "xdcc batch mygroup",
        "xdcc batch 1-10*pattern",
        "xdcc send 1-600",
        "xdcc send #7",
        "xdcc send 7",
        "xdcc ssend 7",
        "xdcc cancel",
        "hello world",
    ],
)
async def test_queue_send_passes_non_expandable_messages_through(bot, message):
    """Test group names, pattern filters, oversized ranges and plain messages stay verbatim."""
    with patch("dccbot.ircbot.asyncio.create_task") as mock_create_task:
        mock_create_task.return_value = MagicMock()
        await bot.queue_send({"user": "MyBot", "message": message})
        mock_create_task.call_args[0][0].close()

    assert _queued_messages(bot) == [message]


@pytest.mark.asyncio
async def test_process_queued_send_does_not_drop_long_waiting_item(bot):
    """Test _process_queued_send still sends an item that waited in the queue past send_queue_max_wait."""
    bot.server_config["send_queue_max_wait"] = 1
    data = {"user": "mybot", "message": "xdcc send #1", "queued_at": time.time() - 10}

    with (
        patch("dccbot.ircbot.handle_send_command", new_callable=AsyncMock) as mock_send,
        patch.object(bot, "_wait_for_queued_transfer", new_callable=AsyncMock),
    ):
        await bot._process_queued_send("mybot", data)

    mock_send.assert_awaited_once_with(bot, data)


@pytest.mark.asyncio
async def test_process_queued_send_sends_and_waits(bot):
    """Test _process_queued_send sends the command and waits for it to settle."""
    data = {"user": "mybot", "message": "xdcc send #1", "queued_at": time.time()}

    with (
        patch("dccbot.ircbot.handle_send_command", new_callable=AsyncMock) as mock_send,
        patch.object(bot, "_wait_for_queued_transfer", new_callable=AsyncMock) as mock_wait,
    ):
        await bot._process_queued_send("mybot", data)

    mock_send.assert_awaited_once_with(bot, data)
    mock_wait.assert_awaited_once()
    assert mock_wait.call_args[0][0] == "mybot"


@pytest.mark.asyncio
async def test_process_queued_send_bumps_last_active(bot):
    """Test _process_queued_send refreshes last_active, since sends bypass process_command_queue."""
    bot.last_active = 0
    data = {"user": "mybot", "message": "xdcc send #1", "queued_at": time.time()}

    with (
        patch("dccbot.ircbot.handle_send_command", new_callable=AsyncMock),
        patch.object(bot, "_wait_for_queued_transfer", new_callable=AsyncMock),
    ):
        await bot._process_queued_send("mybot", data)

    assert bot.last_active >= data["queued_at"]


def test_find_transfer_since_matches_nick_server_and_time(bot, mock_bot_manager):
    """Test _find_transfer_since only matches records for the right nick/server/time."""
    now = time.time()
    mock_bot_manager.transfers = {
        "old.mkv": [{"nick": "mybot", "server": bot.server, "start_time": now - 100}],
        "other.mkv": [{"nick": "otherbot", "server": bot.server, "start_time": now}],
        "match.mkv": [{"nick": "mybot", "server": bot.server, "start_time": now}],
    }

    found = bot._find_transfer_since("mybot", now - 1)
    assert found is mock_bot_manager.transfers["match.mkv"][0]


def test_find_transfer_since_active_only_skips_terminal(bot, mock_bot_manager):
    """Test _find_transfer_since(active_only=True) skips transfers in a terminal status."""
    now = time.time()
    running = {"nick": "mybot", "server": bot.server, "start_time": now, "status": "in_progress"}
    mock_bot_manager.transfers = {
        "done.mkv": [{"nick": "mybot", "server": bot.server, "start_time": now, "status": "completed"}],
        "running.mkv": [running],
    }

    assert bot._find_transfer_since("mybot", now - 1, active_only=True) is running
    mock_bot_manager.transfers = {"done.mkv": mock_bot_manager.transfers["done.mkv"]}
    assert bot._find_transfer_since("mybot", now - 1, active_only=True) is None


def test_find_transfer_since_no_match(bot, mock_bot_manager):
    """Test _find_transfer_since returns None when nothing matches."""
    mock_bot_manager.transfers = {}
    assert bot._find_transfer_since("mybot", time.time()) is None


class FakeClock:
    """Deterministic stand-in for time.time/asyncio.sleep: sleeping advances the clock.

    `on_tick(now)` runs after every sleep, letting a test change transfer state over time.
    """

    def __init__(self, start: float, on_tick=None):
        self.now = start
        self.on_tick = on_tick

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.on_tick:
            self.on_tick(self.now)


async def _run_wait_for_queued_transfer(bot, clock: FakeClock, sent_at: float) -> None:
    """Run _wait_for_queued_transfer("mybot", sent_at) against a FakeClock."""
    with (
        patch("dccbot.ircbot.time.time", side_effect=clock.time),
        patch("dccbot.ircbot.asyncio.sleep", new_callable=AsyncMock, side_effect=clock.sleep),
    ):
        await bot._wait_for_queued_transfer("mybot", sent_at)


def _queued_transfer(bot, start_time: float, status: str = "in_progress", transfer_id: str = "t1") -> dict:
    return {"id": transfer_id, "nick": "mybot", "server": bot.server, "start_time": start_time, "status": status, "bytes_received": 0}


@pytest.mark.asyncio
async def test_wait_for_queued_transfer_no_transfer_appears(bot, mock_bot_manager):
    """With no transfer at all, the wait is send_queue_delay plus a send_queue_cooldown quiet period."""
    mock_bot_manager.transfers = {}
    bot.server_config.update({"send_queue_delay": 15, "send_queue_cooldown": 5})
    clock = FakeClock(1000.0)

    await _run_wait_for_queued_transfer(bot, clock, 1000.0)

    assert 1020.0 <= clock.now < 1021.0


@pytest.mark.asyncio
async def test_wait_for_queued_transfer_no_delay_no_cooldown_returns_immediately(bot, mock_bot_manager):
    """With no transfer and both timings at 0, nothing is awaited."""
    mock_bot_manager.transfers = {}
    bot.server_config.update({"send_queue_delay": 0, "send_queue_cooldown": 0})

    with patch("dccbot.ircbot.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await bot._wait_for_queued_transfer("mybot", time.time())

    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_wait_for_queued_transfer_enforces_minimum_delay(bot, mock_bot_manager):
    """`send_queue_delay` is a minimum gap between sends, even if the transfer already finished."""
    mock_bot_manager.transfers = {"movie.mkv": [_queued_transfer(bot, 1000.0, status="completed")]}
    bot.server_config.update({"send_queue_delay": 10, "send_queue_cooldown": 0})
    clock = FakeClock(1000.0)

    await _run_wait_for_queued_transfer(bot, clock, 1000.0)

    assert clock.now == 1010.0


@pytest.mark.asyncio
async def test_wait_for_queued_transfer_waits_for_terminal_status(bot, mock_bot_manager):
    """The wait lasts until the transfer completes, then the cooldown quiet period."""
    transfer = _queued_transfer(bot, 1000.0)
    mock_bot_manager.transfers = {"movie.mkv": [transfer]}
    bot.server_config.update({"send_queue_delay": 0, "send_queue_cooldown": 5})

    def on_tick(now: float) -> None:
        transfer["bytes_received"] += 1
        if now >= 1030.0:
            transfer["status"] = "completed"

    clock = FakeClock(1000.0, on_tick)
    await _run_wait_for_queued_transfer(bot, clock, 1000.0)

    assert 1035.0 <= clock.now < 1036.0


@pytest.mark.asyncio
async def test_wait_for_queued_transfer_covers_gap_between_batch_files(bot, mock_bot_manager):
    """An `xdcc batch` leaves a short gap between files (seen live: ~1s): a transfer starting during
    the cooldown quiet period restarts it, so the queue waits for the whole batch."""
    first = _queued_transfer(bot, 1000.0, transfer_id="ep1")
    mock_bot_manager.transfers = {"ep1.mkv": [first]}
    bot.server_config.update({"send_queue_delay": 0, "send_queue_cooldown": 5})
    second = _queued_transfer(bot, 1021.0, transfer_id="ep2")

    def on_tick(now: float) -> None:
        for transfer in (first, second):
            transfer["bytes_received"] += 1
        if now >= 1020.0:
            first["status"] = "completed"
        if now >= 1021.0:
            mock_bot_manager.transfers["ep2.mkv"] = [second]
        if now >= 1040.0:
            second["status"] = "completed"

    clock = FakeClock(1000.0, on_tick)
    await _run_wait_for_queued_transfer(bot, clock, 1000.0)

    # Not ~1025 (end of the first file + cooldown): waited for the second file too.
    assert 1045.0 <= clock.now < 1046.0


@pytest.mark.asyncio
async def test_wait_for_queued_transfer_long_progressing_batch_outlasts_max_wait(bot, mock_bot_manager):
    """`send_queue_max_wait` bounds a stall, not the total: a transfer that keeps progressing
    is waited for even well past send_queue_max_wait."""
    transfer = _queued_transfer(bot, 1000.0)
    mock_bot_manager.transfers = {"big.mkv": [transfer]}
    bot.server_config.update({"send_queue_delay": 0, "send_queue_cooldown": 0, "send_queue_max_wait": 60})

    def on_tick(now: float) -> None:
        transfer["bytes_received"] += 1
        if now >= 1200.0:
            transfer["status"] = "completed"

    clock = FakeClock(1000.0, on_tick)
    await _run_wait_for_queued_transfer(bot, clock, 1000.0)

    assert 1200.0 <= clock.now < 1201.0


@pytest.mark.asyncio
async def test_wait_for_queued_transfer_bounded_by_max_wait_without_progress(bot, mock_bot_manager):
    """A transfer stuck in a non-terminal status with no progress (e.g. a stray pack announcement
    with no follow-up DCC SEND) must not stall the queue - `send_queue_max_wait` bounds it."""
    transfer = _queued_transfer(bot, 1000.0, status="started")
    mock_bot_manager.transfers = {"movie.mkv": [transfer]}
    bot.server_config.update({"send_queue_delay": 0, "send_queue_cooldown": 2, "send_queue_max_wait": 60})
    clock = FakeClock(1000.0)

    await _run_wait_for_queued_transfer(bot, clock, 1000.0)

    # Gave up after 60s without progress, then the cooldown.
    assert 1062.0 <= clock.now < 1063.0
    assert transfer["status"] == "started"


@pytest.mark.asyncio
async def test_process_send_queue_processes_items_in_order(bot, mock_bot_manager):
    """Test _process_send_queue drains queued sends one at a time, in order."""
    mock_bot_manager.transfers = {}
    bot.server_config["send_queue_delay"] = 0
    bot.server_config["send_queue_cooldown"] = 0
    sent: list[str] = []

    async def fake_handle_send_command(_bot, data):
        sent.append(data["message"])

    with patch("dccbot.ircbot.handle_send_command", side_effect=fake_handle_send_command):
        await bot.queue_send({"user": "mybot", "message": "xdcc send #1"})
        await bot.queue_send({"user": "mybot", "message": "xdcc send #2"})
        task = bot.send_queue_tasks["mybot"]
        await asyncio.wait_for(bot.send_queues["mybot"].join(), timeout=2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert sent == ["xdcc send #1", "xdcc send #2"]


@pytest.mark.asyncio
async def test_queue_send_tracks_pending_items(bot):
    """Test queue_send records each item in send_queue_items for later listing."""
    with patch("dccbot.ircbot.asyncio.create_task") as mock_create_task:
        mock_create_task.return_value = MagicMock()
        await bot.queue_send({"user": "MyBot", "message": "xdcc send #1", "channels": ["#chan"]})
        await bot.queue_send({"user": "mybot", "message": "xdcc send #2"})
        mock_create_task.call_args[0][0].close()

    assert [item["message"] for item in bot.send_queue_items["mybot"]] == ["xdcc send #1", "xdcc send #2"]


def test_queue_snapshot_reports_pending_items(bot):
    """Test queue_snapshot exposes message/channels/queued_at for each pending item."""
    bot.send_queue_items = {"mybot": [{"message": "xdcc send #1", "channels": ["#chan"], "queued_at": 123.0, "user": "mybot"}]}
    assert bot.queue_snapshot() == {"mybot": [{"message": "xdcc send #1", "channels": ["#chan"], "queued_at": 123.0}]}


def test_queue_snapshot_empty(bot):
    """Test queue_snapshot returns an empty dict when nothing is queued."""
    assert bot.queue_snapshot() == {}


def test_cancel_queued_send_no_items(bot):
    """Test cancel_queued_send returns an empty list when the nick has nothing queued."""
    assert bot.cancel_queued_send("mybot") == []


def test_cancel_queued_send_all(bot):
    """Test cancel_queued_send with no selector cancels and clears every pending item."""
    item1 = {"message": "xdcc send #1"}
    item2 = {"message": "xdcc send #2"}
    bot.send_queue_items = {"mybot": [item1, item2]}

    cancelled = bot.cancel_queued_send("mybot")

    assert cancelled == [item1, item2]
    assert item1["cancelled"] is True
    assert item2["cancelled"] is True
    assert "mybot" not in bot.send_queue_items


def test_cancel_queued_send_by_index(bot):
    """Test cancel_queued_send narrows to a single item by 1-based position."""
    item1 = {"message": "xdcc send #1"}
    item2 = {"message": "xdcc send #2"}
    bot.send_queue_items = {"mybot": [item1, item2]}

    cancelled = bot.cancel_queued_send("mybot", "2")

    assert cancelled == [item2]
    assert item2["cancelled"] is True
    assert "cancelled" not in item1
    assert bot.send_queue_items["mybot"] == [item1]


def test_cancel_queued_send_by_message_substring(bot):
    """Test cancel_queued_send narrows to a single item by matching its message text."""
    item1 = {"message": "xdcc send #1"}
    item2 = {"message": "xdcc send #2"}
    bot.send_queue_items = {"mybot": [item1, item2]}

    cancelled = bot.cancel_queued_send("mybot", "SEND #2")

    assert cancelled == [item2]
    assert bot.send_queue_items["mybot"] == [item1]


def test_cancel_queued_send_no_match(bot):
    """Test cancel_queued_send returns an empty list when the selector matches nothing."""
    item1 = {"message": "xdcc send #1"}
    bot.send_queue_items = {"mybot": [item1]}

    assert bot.cancel_queued_send("mybot", "99") == []
    assert bot.cancel_queued_send("mybot", "nope") == []
    assert bot.send_queue_items["mybot"] == [item1]


def test_untrack_queued_send_removes_by_identity(bot):
    """Test _untrack_queued_send removes the matching item and cleans up an empty list."""
    item = {"message": "xdcc send #1"}
    bot.send_queue_items = {"mybot": [item]}

    bot._untrack_queued_send("mybot", item)

    assert "mybot" not in bot.send_queue_items


def test_untrack_queued_send_missing_is_noop(bot):
    """Test _untrack_queued_send does nothing when the item was already removed (e.g. cancelled)."""
    bot.send_queue_items = {}
    bot._untrack_queued_send("mybot", {"message": "xdcc send #1"})
    assert bot.send_queue_items == {}


@pytest.mark.asyncio
async def test_process_queued_send_skips_cancelled_item(bot):
    """Test _process_queued_send does not send an item that was cancelled before it was dequeued."""
    data = {"user": "mybot", "message": "xdcc send #1", "queued_at": time.time(), "cancelled": True}

    with patch("dccbot.ircbot.handle_send_command", new_callable=AsyncMock) as mock_send:
        await bot._process_queued_send("mybot", data)

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_process_send_queue_untracks_dequeued_item(bot, mock_bot_manager):
    """Test _process_send_queue removes each item from send_queue_items as it's dequeued."""
    mock_bot_manager.transfers = {}
    bot.server_config["send_queue_delay"] = 0
    bot.server_config["send_queue_cooldown"] = 0

    with patch("dccbot.ircbot.handle_send_command", new_callable=AsyncMock):
        await bot.queue_send({"user": "mybot", "message": "xdcc send #1"})
        assert bot.send_queue_items["mybot"]
        task = bot.send_queue_tasks["mybot"]
        await asyncio.wait_for(bot.send_queues["mybot"].join(), timeout=2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert "mybot" not in bot.send_queue_items
