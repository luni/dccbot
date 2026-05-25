"""Integration tests for XDCC file download functionality.

These tests connect to a local iroffer XDCC bot to test file downloads.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dccbot.ircbot import IRCBot

# XDCC bot configuration
XDCC_BOT_NICK = "xdccbot"
TEST_CHANNEL = "#test"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_xdcc_file_list(irc_bot_factory):
    """Test requesting file list from XDCC bot."""
    bot = irc_bot_factory()
    responses: list[str] = []
    original_on_privnotice = bot.on_privnotice

    def capture_on_privnotice(connection, event):
        if getattr(event.source, "nick", None) == XDCC_BOT_NICK:
            responses.append(event.arguments[0])
        return original_on_privnotice(connection, event)

    bot.on_privnotice = capture_on_privnotice

    try:
        await asyncio.wait_for(bot.connect(), timeout=30.0)
        assert bot.connection is not None
        assert bot.connection.connected

        # Wait for welcome before joining channel
        await asyncio.sleep(2)

        await bot.join_channel(TEST_CHANNEL)
        await asyncio.sleep(3)
        assert TEST_CHANNEL in bot.joined_channels

        # Request file list from XDCC bot
        bot.connection.privmsg(XDCC_BOT_NICK, "xdcc list")
        await asyncio.sleep(3)

        # Verify we received at least one response from the XDCC bot
        assert any(
            XDCC_BOT_NICK in r or "listing" in r.lower() for r in responses
        ), f"Expected response from {XDCC_BOT_NICK}, got: {responses}"

    finally:
        if bot.connection and bot.connection.connected:
            await bot.part_channel(TEST_CHANNEL, "Test complete")
            await bot.disconnect("Test complete")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_xdcc_download_file(irc_bot_factory, irc_bot_manager):
    """Test downloading a file from XDCC bot."""
    irc_bot_manager.config["allow_private_ips"] = True

    server_config = {
        "nick": "testxdcc",
        "port": 6667,
        "use_tls": False,
        "channels": [],
        "random_nick": True,
        "verify_ssl": False,
    }

    bot = IRCBot(
        server="localhost",
        server_config=server_config,
        download_path=irc_bot_manager.config["default_download_path"],
        allowed_mimetypes=None,
        max_file_size=1073741824,
        bot_manager=irc_bot_manager,
    )

    try:
        await asyncio.wait_for(bot.connect(), timeout=30.0)
        assert bot.connection is not None
        assert bot.connection.connected

        await asyncio.sleep(2)
        await bot.join_channel(TEST_CHANNEL)
        await asyncio.sleep(3)
        assert TEST_CHANNEL in bot.joined_channels

        # Request first file from XDCC bot
        bot.connection.privmsg(XDCC_BOT_NICK, "xdcc send 1")

        # Wait for DCC transfer to complete
        completed = False
        for _ in range(60):
            await asyncio.sleep(0.5)
            for transfer_list in irc_bot_manager.transfers.values():
                for transfer in transfer_list:
                    if transfer.get("status") == "completed":
                        completed = True
                        break
                if completed:
                    break
            if completed:
                break

        assert completed, f"XDCC transfer did not complete: {irc_bot_manager.transfers}"

        # Verify the downloaded file
        download_path = Path(irc_bot_manager.config["default_download_path"])
        downloaded = list(download_path.iterdir())
        assert len(downloaded) == 1
        assert downloaded[0].stat().st_size == 524288

    finally:
        if bot.connection and bot.connection.connected:
            await bot.part_channel(TEST_CHANNEL, "Test complete")
            await bot.disconnect("Test complete")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_xdcc_download_nonexistent_pack(irc_bot_factory):
    """Test requesting a non-existent pack number."""
    bot = irc_bot_factory()

    try:
        await asyncio.wait_for(bot.connect(), timeout=30.0)
        assert bot.connection is not None
        assert bot.connection.connected

        await asyncio.sleep(2)
        await bot.join_channel(TEST_CHANNEL)
        await asyncio.sleep(3)
        assert TEST_CHANNEL in bot.joined_channels

        # Request invalid pack number
        bot.connection.privmsg(XDCC_BOT_NICK, "xdcc send 999")
        await asyncio.sleep(3)

        # Verify no active DCC transfers were initiated
        assert len(bot.current_transfers) == 0, "Should not have started a transfer for non-existent pack"

    finally:
        if bot.connection and bot.connection.connected:
            await bot.part_channel(TEST_CHANNEL, "Test complete")
            await bot.disconnect("Test complete")
