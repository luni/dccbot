"""Shared test fixtures for dccbot tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp import ClientWebSocketResponse
from aiohttp.test_utils import TestClient

from dccbot.app import IRCBotAPI


@pytest.fixture
def fresh_event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Provide a fresh event loop without redefining pytest-asyncio's fixture."""

    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


@pytest.fixture
def loop_patch(monkeypatch: pytest.MonkeyPatch, fresh_event_loop: asyncio.AbstractEventLoop) -> asyncio.AbstractEventLoop:
    """Patch asyncio.get_event_loop to return a dedicated loop for object creation."""

    monkeypatch.setattr(asyncio, "get_event_loop", lambda: fresh_event_loop)
    return fresh_event_loop


@pytest.fixture
def config_file_factory(tmp_path: Path) -> Callable[[dict[str, Any]], str]:
    """Factory to persist temporary JSON config files for tests."""

    def _write_config(data: dict[str, Any]) -> str:
        path = tmp_path / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    return _write_config


@pytest_asyncio.fixture
async def api_client(aiohttp_client: Callable[[Any], Awaitable[TestClient]]) -> tuple[TestClient, AsyncMock]:
    """Create an API client with a mocked bot manager.

    Args:
        aiohttp_client: The aiohttp test client fixture.

    Returns:
        tuple: A tuple containing the test client and the mocked bot manager.

    """
    mock_bot_manager = AsyncMock()
    api = IRCBotAPI(config_file="config.json", bot_manager=mock_bot_manager)
    client = await aiohttp_client(api.app)
    return client, mock_bot_manager


@pytest_asyncio.fixture
async def ws_session(api_client) -> AsyncGenerator[tuple[ClientWebSocketResponse, AsyncMock], None]:
    """Yield an open websocket connection and close it automatically."""

    client, mock_bot_manager = api_client
    ws = await client.ws_connect("/ws")
    try:
        yield ws, mock_bot_manager
    finally:
        await ws.close()
