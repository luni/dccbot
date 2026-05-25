"""Integration tests for IRC connection functionality.

These tests connect to a local InspIRCd instance via docker-compose.

To run these tests locally:
  1. Start the IRC server: docker-compose up -d ircd
  2. Run tests: pytest tests/integration -m integration
  3. Stop server: docker-compose down

In CI, the GitHub Actions workflow handles starting the IRC server.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import MagicMock

import pytest

from dccbot.ircbot import IRCBot
from dccbot.manager import IRCBotManager

# Local InspIRCd server for testing (started via docker-compose)
TEST_SERVERS = [
    ("localhost", 6667, False),
    ("localhost", 6697, True),
]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("server,port,use_tls", TEST_SERVERS)
async def test_irc_connection(irc_bot_factory, server: str, port: int, use_tls: bool):
    """Test that the bot can connect to a real IRC server.

    This test validates:
    - TCP connection establishment
    - IRC protocol handshake
    - Nick registration
    """
    bot = irc_bot_factory(server=server, port=port, use_tls=use_tls)

    try:
        # Attempt to connect with timeout
        await asyncio.wait_for(bot.connect(), timeout=30.0)

        # Verify connection was established
        assert bot.connection is not None
        assert bot.connection.connected

    finally:
        # Clean up connection
        if bot.connection and bot.connection.connected:
            await bot.disconnect("Test complete")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_irc_connection_with_random_nick(irc_bot_manager):
    """Test connection with randomly generated nickname."""
    unique_nick = f"dccbot_test_{uuid.uuid4().hex[:8]}"
    server_config = {
        "nick": unique_nick,
        "port": 6667,
        "use_tls": False,
        "channels": [],
        "random_nick": True,
    }

    bot = IRCBot(
        server="localhost",
        server_config=server_config,
        download_path=irc_bot_manager.config.get("default_download_path", "/tmp"),
        allowed_mimetypes=irc_bot_manager.config.get("allowed_mimetypes"),
        max_file_size=irc_bot_manager.config.get("max_file_size", 1073741824),
        bot_manager=irc_bot_manager,
    )

    try:
        await asyncio.wait_for(bot.connect(), timeout=30.0)
        assert bot.connection is not None
        assert bot.connection.connected
        # Verify random suffix was added
        assert len(bot.nick) > len(unique_nick)

    finally:
        if bot.connection and bot.connection.connected:
            await bot.disconnect("Test complete")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_irc_channel_join(irc_bot_factory):
    """Test that the bot can join a channel after connecting."""
    bot = irc_bot_factory()
    test_channel = "#test"

    try:
        # Connect first
        await asyncio.wait_for(bot.connect(), timeout=30.0)
        assert bot.connection is not None
        assert bot.connection.connected

        # Join the test channel - should not raise exception
        await bot.join_channel(test_channel)

        # Wait a moment for join to process
        await asyncio.sleep(1)

        # Verify no exception was raised (channel join command was sent successfully)

    finally:
        if bot.connection and bot.connection.connected:
            await bot.part_channel(test_channel, "Test complete")
            await bot.disconnect("Test complete")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_irc_authentication_timeout(tmp_path):
    """Test that connection times out appropriately when server is unreachable."""
    import json

    # Create a valid config file
    config = {
        "servers": {},
        "default_download_path": str(tmp_path / "downloads"),
        "allowed_mimetypes": None,
        "max_file_size": 1073741824,
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config))

    server_config = {
        "nick": "testbot",
        "port": 6667,
        "use_tls": False,
        "channels": [],
        "random_nick": False,
    }

    manager = IRCBotManager(str(config_file))

    bot = IRCBot(
        server="192.0.2.1",  # TEST-NET-1, should not be reachable
        server_config=server_config,
        download_path=str(tmp_path / "downloads"),
        allowed_mimetypes=None,
        max_file_size=1073741824,
        bot_manager=manager,
    )

    # Connection should fail quickly with OSError (network unreachable) or timeout
    with pytest.raises((asyncio.TimeoutError, OSError)):
        await asyncio.wait_for(bot.connect(), timeout=5.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_passive_dcc_listen_setup(irc_bot_factory):
    """Test that passive DCC creates a listening socket and sends a CTCP reply.

    This test validates:
    - Bot connects to IRC server
    - Upon receiving a DCC SEND with port=0, bot starts a listener
    - Bot sends a CTCP reply with its IP and port
    - A peer can connect to the listening socket
    """
    import socket

    bot = irc_bot_factory()
    bot.server_config["passive_dcc"] = True
    bot.server_config["passive_dcc_listen_ip"] = "127.0.0.1"
    bot.server_config["passive_dcc_port_range"] = [40000, 41000]

    ctcp_replies: list[tuple[str, str]] = []

    try:
        await asyncio.wait_for(bot.connect(), timeout=30.0)
        assert bot.connection is not None
        assert bot.connection.connected

        # Capture CTCP replies
        original_ctcp_reply = bot.connection.ctcp_reply

        def capture_ctcp_reply(target, message):
            ctcp_replies.append((target, message))
            return original_ctcp_reply(target, message)

        bot.connection.ctcp_reply = capture_ctcp_reply

        # Simulate receiving a passive DCC SEND (port=0)
        event = MagicMock()
        event.source = MagicMock()
        event.source.nick = "test_sender"
        event.arguments = ["DCC", 'SEND "test.txt" 0 0 1000']

        bot.on_dcc_send(bot.connection, event, False)

        # Wait for the listener to be set up
        await asyncio.sleep(2)

        # Verify a CTCP reply was sent
        assert len(ctcp_replies) == 1
        target, message = ctcp_replies[0]
        assert target == "test_sender"
        assert message.startswith("DCC SEND")

        # Extract the port from the CTCP reply
        parts = message.split()
        listen_port = int(parts[-2])
        assert 40000 <= listen_port <= 41000

        # Verify the listener is actually accepting connections
        test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test_sock.settimeout(5)
        try:
            test_sock.connect(("127.0.0.1", listen_port))
            # Successfully connected - passive DCC listener is working
        finally:
            test_sock.close()

    finally:
        # Clean up any remaining DCC connections
        for dcc in list(bot.current_transfers):
            try:
                dcc.disconnect("Test complete")
            except Exception:
                pass
        if bot.connection and bot.connection.connected:
            await bot.disconnect("Test complete")
