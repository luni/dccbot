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
