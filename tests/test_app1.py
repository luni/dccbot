import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient
from unittest.mock import AsyncMock, patch
from dccbot.app import IRCBotAPI
from dccbot.manager import IRCBotManager
from dccbot.ircbot import IRCBot


# Fixture to initialize the IRCBotAPI application
@pytest_asyncio.fixture
async def api_client(aiohttp_client):
    api = IRCBotAPI("config.json")
    # Patch the bot manager with an AsyncMock
    mock_bot_manager = AsyncMock()
    api.app["bot_manager"] = mock_bot_manager
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
    assert data == {"channel": ["Missing data for required field."]}

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
    assert data == {"server": ["Missing data for required field."]}

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
    assert data == {"channel": ["Missing data for required field."]}

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
    assert data == {"server": ["Missing data for required field."]}

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
