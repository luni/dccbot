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


def _completed_sizes(bot_manager) -> set[int]:
    """Return the sizes of all completed transfers known to the manager."""
    return {
        transfer["size"]
        for transfer_list in bot_manager.transfers.values()
        for transfer in transfer_list
        if transfer.get("status") == "completed" and "size" in transfer
    }


async def _wait_for_completed_sizes(bot_manager, count: int, attempts: int = 90) -> set[int]:
    """Poll until at least ``count`` transfers completed, returning their sizes."""
    completed_sizes: set[int] = set()
    for _ in range(attempts):
        await asyncio.sleep(0.5)
        completed_sizes = _completed_sizes(bot_manager)
        if len(completed_sizes) >= count:
            break
    return completed_sizes


def _capturing_privmsg(original_privmsg, sent_at: dict[str, float]):
    """Wrap ``privmsg`` to record when each message to the XDCC bot actually went out."""

    def capture_privmsg(target: str, text: str) -> None:
        if target == XDCC_BOT_NICK and text not in sent_at:
            sent_at[text] = asyncio.get_event_loop().time()
        original_privmsg(target, text)

    return capture_privmsg


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
async def test_xdcc_queued_sends_are_throttled(irc_bot_manager):
    """Test two queued `xdcc send` requests to the same bot go out one at a time.

    Regression coverage for the send-queue (issue #23): without throttling, both
    `xdcc send` privmsgs would go out back-to-back and iroffer would hand back two
    concurrent DCC transfers immediately. With the queue, the second privmsg must
    wait at least `send_queue_delay`, then for the first transfer to settle, plus
    `send_queue_cooldown` before going out, even though both sends are queued at
    (almost) the same instant.
    """
    irc_bot_manager.config["allow_private_ips"] = True

    server_config = {
        "nick": "testxdccqueue",
        "port": 6667,
        "use_tls": False,
        "channels": [],
        "random_nick": True,
        "verify_ssl": False,
        "send_queue_delay": 10,
        "send_queue_cooldown": 3,
        "send_queue_max_wait": 60,
    }

    bot = IRCBot(
        server="localhost",
        server_config=server_config,
        download_path=irc_bot_manager.config["default_download_path"],
        allowed_mimetypes=None,
        max_file_size=1073741824,
        bot_manager=irc_bot_manager,
    )

    sent_at: dict[str, float] = {}
    original_privmsg = None

    try:
        await asyncio.wait_for(bot.connect(), timeout=30.0)
        assert bot.connection is not None
        assert bot.connection.connected

        await asyncio.sleep(2)
        await bot.join_channel(TEST_CHANNEL)
        await asyncio.sleep(3)
        assert TEST_CHANNEL in bot.joined_channels

        original_privmsg = bot.connection.privmsg
        bot.connection.privmsg = _capturing_privmsg(original_privmsg, sent_at)

        # Queue both sends back-to-back; the queue engine must serialize them.
        await bot.queue_send({"user": XDCC_BOT_NICK, "message": "xdcc send 1"})
        await bot.queue_send({"user": XDCC_BOT_NICK, "message": "xdcc send 2"})

        # Wait for both queued transfers to complete.
        completed_sizes = await _wait_for_completed_sizes(irc_bot_manager, 2)

        assert completed_sizes == {524288, 65536}, f"Expected both queued transfers to complete: {irc_bot_manager.transfers}"

        # Both privmsgs must have gone out, and not back-to-back: the queue holds
        # the second one for at least send_queue_delay + send_queue_cooldown.
        assert "xdcc send 1" in sent_at
        assert "xdcc send 2" in sent_at
        assert sent_at["xdcc send 2"] - sent_at["xdcc send 1"] >= 13, "Second queued send went out too soon - throttling not applied"

    finally:
        if original_privmsg is not None:
            bot.connection.privmsg = original_privmsg
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
