"""Integration tests for XDCC file download functionality.

These tests connect to a local iroffer XDCC bot to test file downloads.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import pytest

from dccbot.ircbot import IRCBot
from dccbot.manager import IRCBotManager

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


# XDCC bot configuration
XDCC_BOT_NICK = "xdccbot"
TEST_CHANNEL = "#test"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_xdcc_file_list(irc_bot_factory, irc_bot_manager):
    """Test requesting file list from XDCC bot."""
    bot = irc_bot_factory()
    test_channel = TEST_CHANNEL

    try:
        # Connect to IRC
        await asyncio.wait_for(bot.connect(), timeout=30.0)
        assert bot.connection is not None
        assert bot.connection.connected

        # Join the test channel where XDCC bot is
        await bot.join_channel(test_channel)
        await asyncio.sleep(2)  # Wait for join and XDCC bot to be present

        # Request file list from XDCC bot
        bot.connection.privmsg(XDCC_BOT_NICK, "xdcc list")

        # Wait for response (XDCC bot sends a CTCP notice)
        await asyncio.sleep(2)

        # Verify no error occurred
        # Note: The actual response parsing would require more complex handling
        # This test verifies the message was sent without error

    finally:
        if bot.connection and bot.connection.connected:
            await bot.part_channel(test_channel, "Test complete")
            await bot.disconnect("Test complete")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_xdcc_download_file(tmp_path, irc_bot_manager):
    """Test downloading a file from XDCC bot."""
    import json

    # Create a config with proper download path
    download_path = tmp_path / "downloads"
    download_path.mkdir(parents=True, exist_ok=True)

    # Update the irc_bot_manager's config with our download path
    irc_bot_manager.config["default_download_path"] = str(download_path)
    irc_bot_manager.config["allow_private_ips"] = True

    server_config = {
        "nick": "testxdccbot",
        "port": 6667,
        "use_tls": False,
        "channels": [],  # Don't auto-join, we'll join manually
        "random_nick": True,
        "verify_ssl": False,
    }

    # Create a debug IRCBot that logs when on_join is called
    original_on_welcome = IRCBot.on_welcome

    def debug_on_welcome(self, connection, event):
        print(f"[DEBUG IRCBot.on_welcome] connected to server, nick={self.nick}")
        return original_on_welcome(self, connection, event)

    IRCBot.on_welcome = debug_on_welcome

    class DebugIRCBot(IRCBot):
        def on_join(self, connection, event):
            print(f"[DEBUG IRCBot.on_join] source.nick={event.source.nick}, target={event.target}, my_nick={self.nick}")
            super().on_join(connection, event)

        def on_privmsg(self, connection, event):
            print(f"[DEBUG IRCBot.on_privmsg] from={event.source.nick}, target={event.target}, msg={event.arguments}")
            super().on_privmsg(connection, event)

        def on_ctcp(self, connection, event):
            print(f"[DEBUG IRCBot.on_ctcp] from={event.source.nick}, type={event.arguments[0] if event.arguments else 'NONE'}, args={event.arguments}")
            super().on_ctcp(connection, event)

        def on_dcc_send(self, connection, event, use_ssl):
            print(f"[DEBUG IRCBot.on_dcc_send] from={event.source.nick}, args={event.arguments}, use_ssl={use_ssl}")
            super().on_dcc_send(connection, event, use_ssl)

    bot = DebugIRCBot(
        server="localhost",
        server_config=server_config,
        download_path=str(download_path),
        allowed_mimetypes=None,
        max_file_size=1073741824,
        bot_manager=irc_bot_manager,
    )

    try:
        # Connect to IRC
        await asyncio.wait_for(bot.connect(), timeout=30.0)
        assert bot.connection is not None
        assert bot.connection.connected
        print(f"[DEBUG] Bot connected with nick: {bot.nick}")

        # Join test channel
        await bot.join_channel(TEST_CHANNEL)
        await asyncio.sleep(2)  # Wait for channel join to complete
        print(f"[DEBUG] After join_channel, bot.nick={bot.nick}, channel={TEST_CHANNEL}")

        # Request first file from XDCC bot. Retry because pack registration can
        # lag briefly while iroffer auto-adds files on startup.
        request_deadline = time.time() + 80
        transfer_started = False
        while time.time() < request_deadline:
            if not (bot.connection and bot.connection.connected):
                await asyncio.wait_for(bot.connect(), timeout=30.0)
                await bot.join_channel(TEST_CHANNEL)
            # Use the bot's queue_command interface to send XDCC request via privmsg
            await bot.queue_command({
                "command": "send",
                "user": XDCC_BOT_NICK,
                "message": "xdcc send 1",
                "channels": [],
            })
            print(f"[DEBUG] Queued 'xdcc send 1' to {XDCC_BOT_NICK} via queue_command")
            # Poll more frequently since small files transfer very quickly (0.001 sec)
            for _ in range(10):  # 10 x 0.5s = 5s total, same as before but more granular
                await asyncio.sleep(0.5)
                print(f"[DEBUG] Poll: current_transfers={bot.current_transfers}, manager.transfers={irc_bot_manager.transfers}")
                if bot.current_transfers or irc_bot_manager.transfers:
                    transfer_started = True
                    print(f"[DEBUG] Transfer detected!")
                    break
            if transfer_started:
                break

        assert transfer_started, "XDCC transfer did not start within retry window"

        # Wait for DCC transfer to complete (with timeout)
        # The bot's transfer handler will process the incoming DCC SEND and download
        max_wait = 30  # seconds
        start_time = time.time()

        while time.time() - start_time < max_wait:
            # Check if any transfers have completed
            transfers_completed = False
            for filename, transfer_list in irc_bot_manager.transfers.items():
                for transfer in transfer_list:
                    if transfer.get("status") == "completed":
                        transfers_completed = True
                        downloaded_file = Path(transfer["file_path"])
                        assert downloaded_file.exists(), f"Downloaded file {filename} should exist"
                        assert downloaded_file.stat().st_size == transfer["size"], "File size should match"
                        break
                if transfers_completed:
                    break

            if transfers_completed:
                break

            await asyncio.sleep(1)

        # Verify that at least one transfer completed
        assert any(
            transfer.get("status") == "completed"
            for transfer_list in irc_bot_manager.transfers.values()
            for transfer in transfer_list
        ), "At least one transfer should have completed"

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
        # Connect to IRC
        await asyncio.wait_for(bot.connect(), timeout=30.0)
        assert bot.connection is not None
        assert bot.connection.connected

        # Join test channel
        await bot.join_channel(TEST_CHANNEL)
        await asyncio.sleep(2)

        # Request invalid pack number
        bot.connection.privmsg(XDCC_BOT_NICK, "xdcc send 999")

        # Wait for error response (or lack of DCC SEND)
        await asyncio.sleep(3)

        # Verify no active DCC transfers were initiated
        assert len(bot.current_transfers) == 0, "Should not have started a transfer for non-existent pack"

    finally:
        if bot.connection and bot.connection.connected:
            await bot.part_channel(TEST_CHANNEL, "Test complete")
            await bot.disconnect("Test complete")
