"""Tests for the web interface static routes and WebSocket commands."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_index_returns_html(api_client):
    """GET / should return the main index.html page."""
    client, _ = api_client
    resp = await client.get("/")
    assert resp.status == 200
    text = await resp.text()
    assert "<title>DCCBot</title>" in text
    assert resp.content_type == "text/html"


@pytest.mark.asyncio
async def test_index_html_returns_static_file(api_client):
    """GET /index.html should return the index.html page."""
    client, _ = api_client
    resp = await client.get("/index.html")
    assert resp.status == 200
    text = await resp.text()
    assert "<title>DCCBot</title>" in text
    assert resp.content_type == "text/html"


@pytest.mark.asyncio
async def test_static_ws_js_served(api_client):
    """GET /static/ws.js should return the WebSocket client library."""
    client, _ = api_client
    resp = await client.get("/static/ws.js")
    assert resp.status == 200
    text = await resp.text()
    assert "createDccbotSocket" in text
    assert resp.content_type in ("application/javascript", "text/javascript")


@pytest.mark.asyncio
async def test_static_file_not_found(api_client):
    """GET /static/nonexistent.css should return 404."""
    client, _ = api_client
    resp = await client.get("/static/nonexistent.css")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_index_html_path_traversal_blocked(api_client):
    """GET /index.html with path traversal attempts should return 404."""
    client, _ = api_client
    resp = await client.get("/index.html/../../../etc/passwd")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_websocket_help_command(ws_session):
    """Sending /help via WebSocket should return a help message."""
    ws, _ = ws_session
    await ws.send_str("/help")
    msg = await ws.receive_json(timeout=1)
    assert msg["status"] == "ok"
    assert "Available websocket commands" in msg["message"]


@pytest.mark.asyncio
async def test_websocket_help_specific_command(ws_session):
    """Sending /help join should return join-specific help."""
    ws, _ = ws_session
    await ws.send_str("/help join")
    msg = await ws.receive_json(timeout=1)
    assert msg["status"] == "ok"
    assert "/join" in msg["message"]
    assert "Usage:" in msg["message"]


@pytest.mark.asyncio
async def test_websocket_join_command(ws_session):
    """Sending /join via WebSocket should queue a join command."""
    import asyncio

    ws, mock_bot_manager = ws_session
    mock_bot = AsyncMock()
    mock_bot_manager.get_bot.return_value = mock_bot

    await ws.send_str("/join irc.example.com #test")
    # join command does not send a response back to the websocket
    await asyncio.sleep(0.1)

    mock_bot_manager.get_bot.assert_awaited_once_with("irc.example.com")
    mock_bot.queue_command.assert_awaited_once_with({
        "command": "join",
        "channels": ["#test"],
    })


@pytest.mark.asyncio
async def test_websocket_part_command(ws_session):
    """Sending /part via WebSocket should queue a part command."""
    import asyncio

    ws, mock_bot_manager = ws_session
    mock_bot = AsyncMock()
    mock_bot_manager.get_bot.return_value = mock_bot

    await ws.send_str("/part irc.example.com #test")
    await asyncio.sleep(0.1)

    mock_bot_manager.get_bot.assert_awaited_once_with("irc.example.com")
    mock_bot.queue_command.assert_awaited_once_with({
        "command": "part",
        "channels": ["#test"],
    })


@pytest.mark.asyncio
async def test_websocket_msg_command(ws_session):
    """Sending /msg via WebSocket should queue a send command."""
    import asyncio

    ws, mock_bot_manager = ws_session
    mock_bot = AsyncMock()
    mock_bot_manager.get_bot.return_value = mock_bot

    await ws.send_str("/msg irc.example.com MyUser hello world")
    await asyncio.sleep(0.1)

    mock_bot_manager.get_bot.assert_awaited_once_with("irc.example.com")
    mock_bot.queue_command.assert_awaited_once_with({
        "command": "send",
        "user": "MyUser",
        "message": "hello world",
    })


@pytest.mark.asyncio
async def test_websocket_msgjoin_command(ws_session):
    """Sending /msgjoin via WebSocket should queue a send command with channel."""
    import asyncio

    ws, mock_bot_manager = ws_session
    mock_bot = AsyncMock()
    mock_bot_manager.get_bot.return_value = mock_bot

    await ws.send_str("/msgjoin irc.example.com #test MyUser hello world")
    await asyncio.sleep(0.1)

    mock_bot_manager.get_bot.assert_awaited_once_with("irc.example.com")
    mock_bot.queue_command.assert_awaited_once_with({
        "command": "send",
        "user": "MyUser",
        "channels": ["#test"],
        "message": "hello world",
    })


@pytest.mark.asyncio
async def test_websocket_info_command(ws_session):
    """Sending /info via WebSocket should return a transfer snapshot."""
    ws, mock_bot_manager = ws_session
    mock_bot_manager.transfers = {}

    await ws.send_str("/info")
    msg = await ws.receive_json(timeout=1)
    assert msg["type"] == "transfers"
    assert isinstance(msg["transfers"], list)


@pytest.mark.asyncio
async def test_websocket_unknown_command(ws_session):
    """Sending an unknown command should return an error."""
    ws, _ = ws_session
    await ws.send_str("/unknown")
    msg = await ws.receive_json(timeout=1)
    assert msg["status"] == "error"
    assert "Unknown command" in msg["message"]


@pytest.mark.asyncio
async def test_websocket_not_enough_arguments(ws_session):
    """Sending /join with too few args should return an error."""
    ws, _ = ws_session
    await ws.send_str("/join irc.example.com")
    msg = await ws.receive_json(timeout=1)
    assert msg["status"] == "error"
    assert "Not enough arguments" in msg["message"]
