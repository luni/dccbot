"""Shared fixtures for integration tests."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from dccbot.ircbot import IRCBot
from dccbot.manager import IRCBotManager

# Local InspIRCd server for testing
TEST_IRC_SERVER = "localhost"
TEST_PORT = 6667


@pytest.fixture
def unique_nick() -> str:
    """Generate a unique IRC nickname for testing."""
    return f"dccbot_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


@pytest.fixture
def irc_bot_manager(tmp_path) -> IRCBotManager:
    """Create an IRCBotManager with a temporary config file."""
    import json

    config = {
        "servers": {},
        "download_path": str(tmp_path / "downloads"),
        "allowed_mimetypes": None,
        "max_file_size": 1073741824,
        "server_idle_timeout": 60,
        "channel_idle_timeout": 60,
        "resume_timeout": 30,
        "transfers": {},
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config))

    manager = IRCBotManager(str(config_file))
    return manager


@pytest.fixture
def irc_bot_factory(irc_bot_manager, unique_nick):
    """Factory to create IRCBot instances for testing."""

    def _create_bot(server: str = TEST_IRC_SERVER, port: int = TEST_PORT, use_tls: bool = False) -> IRCBot:
        server_config = {
            "nick": unique_nick,
            "port": port,
            "use_tls": use_tls,
            "verify_ssl": False,  # Disable SSL verification for self-signed test certs
            "channels": [],
            "random_nick": False,
        }
        bot = IRCBot(
            server=server,
            server_config=server_config,
            download_path=irc_bot_manager.config.get("download_path", "/tmp"),
            allowed_mimetypes=irc_bot_manager.config.get("allowed_mimetypes"),
            max_file_size=irc_bot_manager.config.get("max_file_size", 1073741824),
            bot_manager=irc_bot_manager,
        )
        return bot

    return _create_bot


@pytest.fixture
def irc_bot(irc_bot_factory):
    """Create a default IRCBot for testing."""
    return irc_bot_factory()
