"""Shared test fixtures for dccbot tests."""

from unittest.mock import AsyncMock

import pytest_asyncio

from dccbot.app import IRCBotAPI


@pytest_asyncio.fixture
async def api_client(aiohttp_client):
    """Create an API client with a mocked bot manager.

    Args:
        aiohttp_client: The aiohttp test client fixture.

    Returns:
        tuple: A tuple containing the test client and the mocked bot manager.

    """
    mock_bot_manager = AsyncMock()
    # Patch the bot manager with an AsyncMock
    api = IRCBotAPI(config_file="config.json", bot_manager=mock_bot_manager)
    client = await aiohttp_client(api.app)
    return client, mock_bot_manager
