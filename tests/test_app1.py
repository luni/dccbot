from unittest.mock import AsyncMock, patch
import pytest
import pytest_asyncio
from dccbot.app import IRCBotAPI
from dccbot.ircbot import IRCBot
import json
import datetime
from unittest.mock import MagicMock
from dccbot.app import WebSocketLogHandler
from aiohttp import web
import asyncio

# Fixture to initialize the IRCBotAPI application
@pytest_asyncio.fixture
async def api_client(aiohttp_client):
    mock_bot_manager = AsyncMock()
    # Patch the bot manager with an AsyncMock
    api = IRCBotAPI(config_file="config.json", bot_manager=mock_bot_manager)
    client = await aiohttp_client(api.app)
    return client, mock_bot_manager


# Test cases for the handle_join method
@pytest.mark.asyncio
async def test_join_success(api_client):
    client, mock_bot_manager = api_client

    # Mock the get_bot method to return a mock IRCBot
    mock_bot = AsyncMock()
    mock_bot_manager.get_bot.return_value = mock_bot

    # Test a valid join request
    payload = {"server": "irc.example.com", "channel": "#test"}
    resp = await client.post("/join", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert data == {"status": "ok"}

    # Verify the command was queued
    mock_bot.queue_command.assert_called_once_with({"command": "join", "channels": ["#test"]})


@pytest.mark.asyncio
async def test_join_request_missing_channel_data(api_client):
    client, mock_bot_manager = api_client

    # Test a join request with a missing channel
    payload = {"server": "irc.example.com"}
    resp = await client.post("/join", json=payload)
    assert resp.status == 422
    data = await resp.json()
    assert data == {"json": {"channel": ["Missing data for required field."]}}

    # Ensure no commands were queued
    mock_bot_manager.get_bot.assert_not_called()


@pytest.mark.asyncio
async def test_join_request_missing_server_data(api_client):
    client, mock_bot_manager = api_client

    # Test a join request with a missing server
    payload = {"channel": "test"}
    resp = await client.post("/join", json=payload)
    assert resp.status == 422
    data = await resp.json()
    assert data == {"json": {"server": ["Missing data for required field."]}}

    # Ensure no commands were queued
    mock_bot_manager.get_bot.assert_not_called()


@pytest.mark.asyncio
async def test_join_request_invalid_server_data(api_client):
    client, mock_bot_manager = api_client

    # Mock the get_bot method to raise an exception for an invalid server
    mock_bot_manager.get_bot.side_effect = Exception("Server not found")

    # Test a join request with an invalid server
    payload = {"server": "invalid.server", "channel": "#test"}
    resp = await client.post("/join", json=payload)
    assert resp.status == 400
    data = await resp.json()
    assert data["status"] == "error"
    assert "server not found" in data["message"].lower()

    # Verify the get_bot method was called
    mock_bot_manager.get_bot.assert_called_once_with("invalid.server")


@pytest.mark.asyncio
async def test_join_request_exception_during_bot_queue_command(api_client):
    client, mock_bot_manager = api_client

    # Mock the get_bot method to return a mock IRCBot
    mock_bot = AsyncMock()
    mock_bot_manager.get_bot.return_value = mock_bot

    # Mock the queue_command method to raise an exception
    mock_bot.queue_command.side_effect = Exception("Error queuing command")

    # Test a part request with an exception during bot queue command
    payload = {"server": "irc.example.com", "channel": "#test"}
    resp = await client.post("/join", json=payload)
    assert resp.status == 400
    data = await resp.json()
    assert data["status"] == "error"
    assert "error queuing command" in data["message"].lower()


@pytest.mark.asyncio
async def test_join_multiple_channels(api_client):
    client, mock_bot_manager = api_client

    # Mock the get_bot method to return a mock IRCBot
    mock_bot = AsyncMock()
    mock_bot_manager.get_bot.return_value = mock_bot

    # Test a join request with multiple channels
    payload = {"server": "irc.example.com", "channels": ["#test1", "#test2"]}
    resp = await client.post("/join", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert data == {"status": "ok"}

    # Verify the command was queued
    mock_bot.queue_command.assert_called_once_with({"command": "join", "channels": ["#test1", "#test2"]})


@pytest.mark.asyncio
async def test_part_success(api_client):
    client, mock_bot_manager = api_client

    # Mock the get_bot method to return a mock IRCBot
    mock_bot = AsyncMock()
    mock_bot_manager.get_bot.return_value = mock_bot

    # Test a valid part request
    payload = {"server": "irc.example.com", "channel": "#test", "reason": "test reason"}
    resp = await client.post("/part", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert data == {"status": "ok"}

    # Verify the command was queued
    mock_bot.queue_command.assert_called_once_with({
        "command": "part",
        "channels": ["#test"],
        "reason": "test reason",
    })


@pytest.mark.asyncio
async def test_part_request_missing_channel_data(api_client):
    client, mock_bot_manager = api_client

    # Test a part request with missing channel data
    payload = {"server": "irc.example.com"}
    resp = await client.post("/part", json=payload)
    assert resp.status == 422
    data = await resp.json()
    assert data == {"json": {"channel": ["Missing data for required field."]}}

    # Ensure no commands were queued
    mock_bot_manager.get_bot.assert_not_called()


@pytest.mark.asyncio
async def test_part_request_missing_server_data(api_client):
    client, mock_bot_manager = api_client

    # Test a part request with missing server data
    payload = {"channel": "#test"}
    resp = await client.post("/part", json=payload)
    assert resp.status == 422
    data = await resp.json()
    assert data == {"json": {"server": ["Missing data for required field."]}}

    # Ensure no commands were queued
    mock_bot_manager.get_bot.assert_not_called()


@pytest.mark.asyncio
async def test_part_request_invalid_server_data(api_client):
    client, mock_bot_manager = api_client

    # Mock the get_bot method to raise an exception for an invalid server
    mock_bot_manager.get_bot.side_effect = Exception("Server not found")

    # Test a part request with an invalid server
    payload = {"server": "invalid.server", "channel": "#test"}
    resp = await client.post("/part", json=payload)
    assert resp.status == 400
    data = await resp.json()
    assert data["status"] == "error"
    assert "server not found" in data["message"].lower()


@pytest.mark.asyncio
async def test_part_request_exception_during_bot_queue_command(api_client):
    client, mock_bot_manager = api_client

    # Mock the get_bot method to return a mock IRCBot
    mock_bot = AsyncMock()
    mock_bot_manager.get_bot.return_value = mock_bot

    # Mock the queue_command method to raise an exception
    mock_bot.queue_command.side_effect = Exception("Error queuing command")

    # Test a part request with an exception during bot queue command
    payload = {"server": "irc.example.com", "channel": "#test"}
    resp = await client.post("/part", json=payload)
    assert resp.status == 400
    data = await resp.json()
    assert data["status"] == "error"
    assert "error queuing command" in data["message"].lower()


@pytest.mark.asyncio
async def test_part_multiple_channels(api_client):
    client, mock_bot_manager = api_client

    # Mock the get_bot method to return a mock IRCBot
    mock_bot = AsyncMock()
    mock_bot_manager.get_bot.return_value = mock_bot

    # Test a join request with multiple channels
    payload = {"server": "irc.example.com", "channels": ["#test1", "#test2"]}
    resp = await client.post("/part", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert data == {"status": "ok"}

    # Verify the command was queued
    mock_bot.queue_command.assert_called_once_with({"command": "part", "channels": ["#test1", "#test2"], "reason": None})


@pytest.mark.asyncio
async def test_shutdown_request_valid_bot_manager(api_client):
    client, mock_bot_manager = api_client
    mock_bot_manager.bots = {"bot1": AsyncMock(), "bot2": AsyncMock()}
    resp = await client.post("/shutdown")
    assert resp.status == 200
    data = await resp.json()
    assert data == {"status": "ok"}
    for bot in mock_bot_manager.bots.values():
        bot.disconnect.assert_called_once_with("Shutting down")


@pytest.mark.asyncio
async def test_shutdown_request_no_bots(api_client):
    client, mock_bot_manager = api_client
    mock_bot_manager.bots = {}
    resp = await client.post("/shutdown")
    assert resp.status == 200
    data = await resp.json()
    assert data == {"status": "ok"}


@pytest.mark.asyncio
async def test_shutdown_request_exception_during_bot_disconnection(api_client):
    client, mock_bot_manager = api_client
    bot1 = AsyncMock()
    mock_bot_manager.bots = {"bot1": bot1}
    bot1.disconnect.side_effect = Exception("Test exception")
    with patch("dccbot.app.logger") as mock_logger:
        resp = await client.post("/shutdown")
        assert resp.status == 400
        data = await resp.json()
        assert data["status"] == "error"
        mock_logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_request_exception_during_app_shutdown(api_client):
    client, mock_bot_manager = api_client
    mock_bot_manager.bots = {}
    with patch("aiohttp.web.Application.shutdown") as mock_shutdown:
        mock_shutdown.side_effect = Exception("Test exception")
        with patch("dccbot.app.logger") as mock_logger:
            resp = await client.post("/shutdown")
            assert resp.status == 400
            data = await resp.json()
            assert data["status"] == "error"
            mock_logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_info_success_empty_bot_manager(api_client):
    # Mock bot manager with empty bots and transfers
    client, mock_bot_manager = api_client

    mock_bot_manager.bots = {}
    mock_bot_manager.transfers = {}

    # Send request and check response
    resp = await client.get("/info")
    assert resp.status == 200
    data = await resp.json()
    assert data == {"networks": [], "transfers": []}


@pytest.mark.asyncio
async def test_cancel_transfer_success(api_client):
    client, mock_bot_manager = api_client
    mock_bot_manager.cancel_transfer.return_value = True
    payload = {"server": "irc.example.com", "nick": "sender_nick", "filename": "file.txt"}
    resp = await client.post("/cancel", json=payload)
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    assert "cancelled" in data["message"].lower()
    mock_bot_manager.cancel_transfer.assert_awaited_once_with("irc.example.com", "sender_nick", "file.txt")


@pytest.mark.asyncio
async def test_cancel_transfer_not_found(api_client):
    client, mock_bot_manager = api_client
    mock_bot_manager.cancel_transfer.return_value = False
    payload = {"server": "irc.example.com", "nick": "sender_nick", "filename": "file.txt"}
    resp = await client.post("/cancel", json=payload)
    assert resp.status == 400
    data = await resp.json()
    assert data["status"] == "error"
    assert "not found" in data["message"].lower() or "not running" in data["message"].lower()
    mock_bot_manager.cancel_transfer.assert_awaited_once_with("irc.example.com", "sender_nick", "file.txt")


@pytest.mark.asyncio
async def test_cancel_transfer_exception(api_client):
    client, mock_bot_manager = api_client
    mock_bot_manager.cancel_transfer.side_effect = Exception("Test exception")
    payload = {"server": "irc.example.com", "nick": "sender_nick", "filename": "file.txt"}
    resp = await client.post("/cancel", json=payload)
    assert resp.status == 400
    data = await resp.json()
    assert data["status"] == "error"
    assert "test exception" in data["message"].lower()
    mock_bot_manager.cancel_transfer.assert_awaited_once_with("irc.example.com", "sender_nick", "file.txt")

@pytest.mark.asyncio
async def test_info_success_bot_manager_with_bots_and_transfers(api_client):
    # Mock bot manager with bots and transfers
    client, mock_bot_manager = api_client

    bot1 = IRCBot("server1", {}, "download_path", ["mimetype1"], 1000000, mock_bot_manager)
    bot2 = IRCBot("server2", {}, "download_path", ["mimetype2"], 1000000, mock_bot_manager)
    mock_bot_manager.bots = {"server1": bot1, "server2": bot2}
    mock_bot_manager.transfers = {
        "file1": [
            {
                "server": "server1",
                "filename": "file1",
                "nick": "nick1",
                "peer_address": "1.2.3.4",
                "peer_port": 5678,
                "size": 1000,
                "bytes_received": 500,
                "start_time": 1643723400,
                "last_progress_bytes_received": 400,
                "last_progress_update": 1643723405,
                "offset": 0,
                "completed": False,
                "connected": True,
            }
        ],
        "file2": [
            {
                "server": "server2",
                "filename": "file2",
                "nick": "nick2",
                "peer_address": "1.2.3.5",
                "peer_port": 5678,
                "size": 2000,
                "bytes_received": 1000,
                "start_time": 1643723410,
                "last_progress_bytes_received": 600,
                "last_progress_update": 1643723415,
                "offset": 1000,
                "completed": True,
                "connected": False,
            }
        ],
    }

    # Send request and check response
    resp = await client.get("/info")
    assert resp.status == 200
    data = await resp.json()
    assert len(data["networks"]) == 2
    assert len(data["transfers"]) == 2

@pytest.mark.asyncio
async def test_cancel_missing_fields(api_client):
    client, _ = api_client
    # Missing 'nick'
    payload = {"server": "irc.example.com", "filename": "file.txt"}
    resp = await client.post("/cancel", json=payload)
    assert resp.status == 422
    data = await resp.json()
    assert "nick" in data["json"]

@pytest.mark.asyncio
async def test_cancel_wrong_type(api_client):
    client, _ = api_client
    # 'nick' should be a string, not a list
    payload = {"server": "irc.example.com", "nick": ["not", "a", "string"], "filename": "file.txt"}
    resp = await client.post("/cancel", json=payload)
    assert resp.status == 422
    data = await resp.json()
    assert "nick" in data["json"]

@pytest.mark.asyncio
async def test_cancel_internal_error(api_client):
    client, mock_bot_manager = api_client
    mock_bot_manager.cancel_transfer.side_effect = Exception("Internal error!")
    payload = {"server": "irc.example.com", "nick": "nick", "filename": "file.txt"}
    resp = await client.post("/cancel", json=payload)
    assert resp.status == 400
    data = await resp.json()
    assert data["status"] == "error"
    assert "internal error" in data["message"].lower()

@pytest.mark.asyncio
async def test_info_no_bots_no_transfers(api_client):
    client, mock_bot_manager = api_client
    mock_bot_manager.bots = {}
    mock_bot_manager.transfers = {}
    resp = await client.get("/info")
    assert resp.status == 200
    data = await resp.json()
    assert data == {"networks": [], "transfers": []}


@pytest.mark.asyncio
async def test_websocket_log_handler_emit_sends_log(monkeypatch):
    ws_mock = MagicMock()
    ws_mock.closed = False
    sent = {}

    def fake_send_str(msg):
        sent['msg'] = msg
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
    handler.format = lambda r: r.getMessage()   # type: ignore

    handler.emit(record)
    # Check that the websocket received a JSON log message
    assert "msg" in sent
    log_entry = json.loads(sent["msg"])
    assert log_entry["level"] == "INFO"
    assert log_entry["message"] == "Test log"
    assert "timestamp" in log_entry

@pytest.mark.asyncio
async def test_websocket_log_handler_removes_closed_ws(monkeypatch):
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
    handler.format = lambda r: r.getMessage()   # type: ignore

    handler.emit(record)
    # ws_closed should be removed from handler.websockets
    assert ws_closed not in handler.websockets
    # ws_open should remain
    assert ws_open in handler.websockets


@pytest.mark.asyncio
async def test_return_static_html_returns_html(api_client):
    client, _ = api_client
    # This assumes the route '/log.html' is mapped to _return_static_html
    resp = await client.get("/log.html")
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("text/html")
    text = await resp.text()
    assert "<html" in text.lower()  # crude check for HTML content



@pytest.mark.asyncio
async def test_websocket_handler_lifecycle(api_client):
    client, _ = api_client
    ws = await client.ws_connect("/ws")

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
async def test_websocket_handler_invalid_command(api_client):
    client, _ = api_client
    ws = await client.ws_connect("/ws")
    # Send an unknown command
    await ws.send_str("/foobar")
    # The server should not crash, but may not respond (depends on your handler)
    # Optionally, check for logs or just ensure connection stays open
    await ws.close()
    assert ws.closed

@pytest.mark.asyncio
async def test_websocket_handler_part_not_enough_args(api_client):
    client, _ = api_client
    ws = await client.ws_connect("/ws")
    # Send /part with not enough args
    await ws.send_str("/part onlyone")
    msg = await ws.receive(timeout=2)
    assert msg.type == web.WSMsgType.TEXT
    data = msg.json()
    assert data["status"] == "error"
    assert "not enough arguments" in data["message"].lower()
    await ws.close()

@pytest.mark.asyncio
async def test_websocket_handler_msg_not_enough_args(api_client):
    client, _ = api_client
    ws = await client.ws_connect("/ws")
    # Send /msg with not enough args
    await ws.send_str("/msg onlyone")
    msg = await ws.receive(timeout=2)
    assert msg.type == web.WSMsgType.TEXT
    data = msg.json()
    assert data["status"] == "error"
    assert "not enough arguments" in data["message"].lower()
    await ws.close()

@pytest.mark.asyncio
async def test_websocket_handler_join_success(api_client):
    from unittest.mock import AsyncMock, MagicMock
    client, mock_bot_manager = api_client
    ws = await client.ws_connect("/ws")
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
    client, _ = api_client
    ws = await client.ws_connect("/ws")
    # Wait for a bit longer than the ping interval (10s in your code)
    await asyncio.sleep(11)
    # The websocket client should have received a ping from the server
    # aiohttp's ws_connect automatically responds to ping with pong, but we can check the connection is still alive
    assert not ws.closed
    await ws.close()

@pytest.mark.asyncio
async def test_websocket_handler_client_sends_pong(api_client, caplog):
    client, _ = api_client
    ws = await client.ws_connect("/ws")
    # Send a pong frame to the server
    await ws.pong()
    # The server should log "Received pong from client" (if logging is configured to show DEBUG)
    # Optionally, you can check logs with caplog if you configure logging
    await asyncio.sleep(0.1)  # Give the server a moment to process
    await ws.close()
    assert ws.closed