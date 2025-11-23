"""Tests for WebSocket functionality and log handler."""

import asyncio
import datetime
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from dccbot.app import WebSocketLogHandler


@pytest.mark.asyncio
async def test_websocket_log_handler_emit_sends_log(monkeypatch):
    """Test that WebSocketLogHandler emits logs to connected websockets."""
    ws_mock = MagicMock()
    ws_mock.closed = False
    sent = {}

    def fake_send_str(msg):
        sent["msg"] = msg

    ws_mock.send_str.side_effect = fake_send_str

    handler = WebSocketLogHandler({ws_mock})

    # Patch asyncio.create_task to run immediately for test
    monkeypatch.setattr("asyncio.create_task", lambda coro: coro)

    # Emit a log record
    record = MagicMock()
    record.levelname = "INFO"
    record.getMessage.return_value = "Test log"
    record.msg = "Test log"
    record.args = ()
    record.created = datetime.datetime.now().timestamp()
    handler.format = lambda r: r.getMessage()  # type: ignore

    handler.emit(record)
    # Check that the websocket received a JSON log message
    assert "msg" in sent
    log_entry = json.loads(sent["msg"])
    assert log_entry["level"] == "INFO"
    assert log_entry["message"] == "Test log"
    assert "timestamp" in log_entry


@pytest.mark.asyncio
async def test_websocket_log_handler_removes_closed_ws(monkeypatch):
    """Test that WebSocketLogHandler removes closed websockets."""
    ws_open = MagicMock()
    ws_open.closed = False
    ws_closed = MagicMock()
    ws_closed.closed = True

    handler = WebSocketLogHandler({ws_open, ws_closed})

    # Patch asyncio.create_task to run immediately for test
    monkeypatch.setattr("asyncio.create_task", lambda coro: coro)

    record = MagicMock()
    record.levelname = "WARNING"
    record.getMessage.return_value = "Closed test"
    handler.format = lambda r: r.getMessage()  # type: ignore

    handler.emit(record)
    # ws_closed should be removed from handler.websockets
    assert ws_closed not in handler.websockets
    # ws_open should remain
    assert ws_open in handler.websockets


@pytest.mark.asyncio
async def test_websocket_handler_lifecycle(ws_session):
    """Test websocket connection lifecycle."""
    ws, _ = ws_session

    # Send a text message (not a command) and expect no crash
    await ws.send_str("Hello world")
    # Optionally, check if a log message is received if your frontend echoes logs

    # Send a command (e.g., /help) and expect a JSON response
    await ws.send_str("/help")
    msg = await ws.receive(timeout=2)
    assert msg.type == web.WSMsgType.TEXT
    data = msg.json()
    assert data["status"] == "ok"
    assert "Available commands" in data["message"]

    # Close the websocket and ensure clean shutdown
    await ws.close()
    assert ws.closed


@pytest.mark.asyncio
async def test_websocket_handler_invalid_command(ws_session):
    """Test websocket with invalid command."""
    ws, _ = ws_session
    # Send an unknown command
    await ws.send_str("/foobar")
    # The server should not crash, but may not respond (depends on your handler)
    # Optionally, check for logs or just ensure connection stays open
    await ws.close()
    assert ws.closed


@pytest.mark.asyncio
async def test_websocket_handler_part_not_enough_args(ws_session):
    """Test websocket /part command with insufficient arguments."""
    ws, _ = ws_session
    # Send /part with not enough args
    await ws.send_str("/part onlyone")
    msg = await ws.receive(timeout=2)
    assert msg.type == web.WSMsgType.TEXT
    data = msg.json()
    assert data["status"] == "error"
    assert "not enough arguments" in data["message"].lower()
    await ws.close()


@pytest.mark.asyncio
async def test_websocket_handler_msg_not_enough_args(ws_session):
    """Test websocket /msg command with insufficient arguments."""
    ws, _ = ws_session
    # Send /msg with not enough args
    await ws.send_str("/msg onlyone")
    msg = await ws.receive(timeout=2)
    assert msg.type == web.WSMsgType.TEXT
    data = msg.json()
    assert data["status"] == "error"
    assert "not enough arguments" in data["message"].lower()
    await ws.close()


@pytest.mark.asyncio
async def test_websocket_handler_join_success(ws_session):
    """Test websocket /join command success."""
    ws, mock_bot_manager = ws_session
    mock_bot = MagicMock()
    mock_bot.queue_command = AsyncMock()
    mock_bot_manager.get_bot = AsyncMock(return_value=mock_bot)
    await ws.send_str("/join server #chan1 #chan2")
    # Await a short time to let the handler process the command
    await asyncio.sleep(0.1)
    mock_bot.queue_command.assert_called_once()
    await ws.close()


@pytest.mark.asyncio
async def test_websocket_handler_server_sends_ping(api_client):
    """Test that websocket server sends ping frames."""
    client, _ = api_client
    ws = await client.ws_connect("/ws")
    # Wait for a bit longer than the ping interval (10s in your code)
    await asyncio.sleep(11)
    # The websocket client should have received a ping from the server
    # aiohttp's ws_connect automatically responds to ping with pong, but we can check the connection is still alive
    assert not ws.closed
    await ws.close()


@pytest.mark.asyncio
async def test_websocket_handler_client_sends_pong(api_client):
    """Test websocket when client sends pong."""
    client, _ = api_client
    ws = await client.ws_connect("/ws")
    # Send a pong frame to the server
    await ws.pong()
    # The server should log "Received pong from client" (if logging is configured to show DEBUG)
    # Optionally, you can check logs with caplog if you configure logging
    await asyncio.sleep(0.1)  # Give the server a moment to process
    await ws.close()
    assert ws.closed


@pytest.mark.asyncio
async def test_websocket_handler_help_command(api_client):
    """Test websocket /help command."""
    client, _ = api_client
    ws = await client.ws_connect("/ws")
    await ws.send_str("/help")
    msg = await ws.receive(timeout=2)
    assert msg.type == web.WSMsgType.TEXT
    data = msg.json()
    assert data["status"] == "ok"
    assert "part" in data["message"].lower()
    assert "join" in data["message"].lower()
    await ws.close()


@pytest.mark.asyncio
async def test_websocket_handler_help_with_command(api_client):
    """Test websocket /help with specific command."""
    client, _ = api_client
    ws = await client.ws_connect("/ws")
    await ws.send_str("/help join")
    msg = await ws.receive(timeout=2)
    assert msg.type == web.WSMsgType.TEXT
    data = msg.json()
    assert data["status"] == "ok"
    assert "join" in data["message"].lower()
    assert "server" in data["message"].lower()
    await ws.close()


@pytest.mark.asyncio
async def test_websocket_handler_msg_command(ws_session):
    """Test websocket /msg command."""
    ws, mock_bot_manager = ws_session
    mock_bot = MagicMock()
    mock_bot.queue_command = AsyncMock()
    mock_bot_manager.get_bot = AsyncMock(return_value=mock_bot)
    await ws.send_str("/msg server target hello world")
    await asyncio.sleep(0.1)
    mock_bot.queue_command.assert_called_once()
    call_args = mock_bot.queue_command.call_args[0][0]
    assert call_args["command"] == "send"
    assert call_args["user"] == "target"
    assert call_args["message"] == "hello world"
    await ws.close()


@pytest.mark.asyncio
async def test_websocket_handler_msgjoin_command(ws_session):
    """Test websocket /msgjoin command."""
    ws, mock_bot_manager = ws_session
    mock_bot = MagicMock()
    mock_bot.queue_command = AsyncMock()
    mock_bot_manager.get_bot = AsyncMock(return_value=mock_bot)
    await ws.send_str("/msgjoin server #channel target hello world")
    await asyncio.sleep(0.1)
    mock_bot.queue_command.assert_called_once()
    call_args = mock_bot.queue_command.call_args[0][0]
    assert call_args["command"] == "send"
    assert call_args["user"] == "target"
    assert call_args["channels"] == ["#channel"]
    assert call_args["message"] == "hello world"
    await ws.close()
