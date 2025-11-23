"""Tests for IRCBotManager class."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dccbot.manager import IRCBotManager, cleanup_background_tasks, start_background_tasks


@pytest.fixture
def config_file():
    """Create a temporary config file for testing."""
    config = {
        "servers": {
            "irc.example.com": {
                "nick": "testbot",
                "channels": ["#test"],
            }
        },
        "default_download_path": "/tmp/downloads",
        "max_file_size": 1000000,
    }
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        json.dump(config, f)
        return f.name


@pytest.fixture
def manager(config_file):
    """Create an IRCBotManager instance for testing."""
    return IRCBotManager(config_file)


def test_load_config_success(manager):
    """Test successful config loading."""
    assert "servers" in manager.config
    assert "irc.example.com" in manager.config["servers"]


def test_load_config_missing_servers():
    """Test config loading with missing servers key."""
    config = {"other_key": "value"}
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        json.dump(config, f)
        config_file = f.name

    with pytest.raises(ValueError, match="Missing 'servers' key"):
        IRCBotManager(config_file)


def test_load_config_invalid_json():
    """Test config loading with invalid JSON."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        f.write("invalid json {")
        config_file = f.name

    with pytest.raises(Exception):
        IRCBotManager(config_file)


@pytest.mark.asyncio
async def test_get_bot_creates_new_bot(manager):
    """Test that get_bot creates a new bot if it doesn't exist."""
    with patch("dccbot.manager.IRCBot") as mock_ircbot:
        mock_bot = AsyncMock()
        mock_ircbot.return_value = mock_bot

        bot = await manager.get_bot("irc.example.com")
        assert bot == mock_bot
        mock_bot.connect.assert_called_once()


@pytest.mark.asyncio
async def test_get_bot_returns_existing_bot(manager):
    """Test that get_bot returns existing bot."""
    with patch("dccbot.manager.IRCBot") as mock_ircbot:
        mock_bot = AsyncMock()
        mock_ircbot.return_value = mock_bot

        bot1 = await manager.get_bot("irc.example.com")
        bot2 = await manager.get_bot("irc.example.com")
        assert bot1 == bot2
        # connect should only be called once
        assert mock_bot.connect.call_count == 1


@pytest.mark.asyncio
async def test_get_bot_unknown_server(manager):
    """Test get_bot with unknown server and no default config."""
    with pytest.raises(ValueError, match="No configuration found"):
        await manager.get_bot("unknown.server")


@pytest.mark.asyncio
async def test_get_bot_with_default_config():
    """Test get_bot with default server config."""
    config = {
        "servers": {},
        "default_server_config": {
            "nick": "defaultbot",
        },
        "default_download_path": "/tmp/downloads",
    }
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        json.dump(config, f)
        config_file = f.name

    manager = IRCBotManager(config_file)

    with patch("dccbot.manager.IRCBot") as mock_ircbot:
        mock_bot = AsyncMock()
        mock_ircbot.return_value = mock_bot

        bot = await manager.get_bot("any.server")
        assert bot == mock_bot


@pytest.mark.asyncio
async def test_cancel_transfer_success(manager):
    """Test successful transfer cancellation."""
    mock_bot = MagicMock()
    mock_dcc = MagicMock()
    transfer = {
        "filename": "test.txt",
        "status": "in_progress",
        "nick": "sender",
    }
    mock_bot.current_transfers = {mock_dcc: transfer}
    manager.bots = {"irc.example.com": mock_bot}
    manager.transfers = {
        "test.txt": [
            {
                "server": "irc.example.com",
                "status": "in_progress",
                "nick": "sender",
            }
        ]
    }

    result = await manager.cancel_transfer("irc.example.com", "sender", "test.txt")
    assert result is True
    assert transfer["status"] == "cancelled"
    assert transfer["error"] == "Cancelled by user"
    mock_dcc.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_transfer_not_found(manager):
    """Test transfer cancellation when transfer not found."""
    mock_bot = MagicMock()
    mock_bot.current_transfers = {}
    manager.bots = {"irc.example.com": mock_bot}

    result = await manager.cancel_transfer("irc.example.com", "sender", "test.txt")
    assert result is False


@pytest.mark.asyncio
async def test_cancel_transfer_bot_not_found(manager):
    """Test transfer cancellation when bot not found."""
    result = await manager.cancel_transfer("unknown.server", "sender", "test.txt")
    assert result is False


@pytest.mark.asyncio
async def test_cancel_transfer_disconnect_exception(manager):
    """Test transfer cancellation with disconnect exception."""
    mock_bot = MagicMock()
    mock_dcc = MagicMock()
    mock_dcc.disconnect.side_effect = Exception("Disconnect failed")
    transfer = {
        "filename": "test.txt",
        "status": "in_progress",
        "nick": "sender",
    }
    mock_bot.current_transfers = {mock_dcc: transfer}
    manager.bots = {"irc.example.com": mock_bot}

    result = await manager.cancel_transfer("irc.example.com", "sender", "test.txt")
    assert result is True
    assert transfer["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cleanup_transfers(manager):
    """Test cleanup of old transfers."""
    import time

    old_time = time.time() - 100000  # Very old
    manager.transfers = {
        "old_file.txt": [
            {
                "start_time": old_time,
            }
        ],
        "new_file.txt": [
            {
                "start_time": time.time(),
            }
        ],
    }
    manager.transfer_list_timeout = 86400

    await manager._cleanup_transfers()

    assert "old_file.txt" not in manager.transfers
    assert "new_file.txt" in manager.transfers


@pytest.mark.asyncio
async def test_cleanup_bots_idle_server(manager):
    """Test cleanup of idle servers."""
    import time

    mock_bot = AsyncMock()
    mock_bot.joined_channels = {}
    mock_bot.current_transfers = {}
    mock_bot.command_queue = AsyncMock()
    mock_bot.command_queue.empty.return_value = True
    mock_bot.last_active = time.time() - 2000
    mock_bot.cleanup = AsyncMock()

    manager.bots = {"irc.example.com": mock_bot}
    manager.server_idle_timeout = 1800

    await manager._cleanup_bots()

    mock_bot.disconnect.assert_called_once_with("Idle timeout")
    assert "irc.example.com" not in manager.bots


@pytest.mark.asyncio
async def test_cleanup_bots_active_server(manager):
    """Test that active servers are not cleaned up."""
    import time

    mock_bot = AsyncMock()
    mock_bot.joined_channels = {"#test": time.time()}
    mock_bot.current_transfers = {}
    mock_bot.command_queue = AsyncMock()
    mock_bot.command_queue.empty.return_value = True
    mock_bot.last_active = time.time()
    mock_bot.cleanup = AsyncMock()

    manager.bots = {"irc.example.com": mock_bot}
    manager.server_idle_timeout = 1800

    await manager._cleanup_bots()

    mock_bot.disconnect.assert_not_called()
    assert "irc.example.com" in manager.bots


def test_get_md5(tmp_path):
    """Test MD5 calculation."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("Hello, World!")

    md5_hash = IRCBotManager.get_md5(str(test_file))
    # MD5 of "Hello, World!" is 65a8e27d8879283831b664bd8b7f0ad4
    assert md5_hash == "65a8e27d8879283831b664bd8b7f0ad4"


@pytest.mark.asyncio
async def test_check_queue_processor(manager):
    """Test MD5 check queue processor."""
    test_file = Path(tempfile.mktemp())
    test_file.write_text("test content")

    transfer_job = {
        "id": "test-id",
        "filename": "test.txt",
        "file_path": str(test_file),
    }

    manager.transfers = {
        "test.txt": [
            {
                "id": "test-id",
            }
        ]
    }

    await manager.md5_check_queue.put(transfer_job)

    # Create a task and let it process one item
    task = asyncio.create_task(manager.check_queue_processor(asyncio.get_running_loop(), manager.md5_check_queue))
    await asyncio.sleep(0.2)
    task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass

    # Check that MD5 was calculated
    assert "file_md5" in manager.transfers["test.txt"][0]

    test_file.unlink()


@pytest.mark.asyncio
async def test_start_background_tasks():
    """Test starting background tasks."""
    app = {}
    mock_manager = MagicMock()
    mock_manager.cleanup = AsyncMock(return_value=None)
    mock_manager.check_queue_processor = AsyncMock(return_value=None)
    mock_manager.md5_check_queue = asyncio.Queue()
    app["bot_manager"] = mock_manager

    await start_background_tasks(app)

    assert "cleanup_task" in app
    assert "queue_processor_task" in app

    # Cancel tasks to clean up
    app["cleanup_task"].cancel()
    app["queue_processor_task"].cancel()
    try:
        await app["cleanup_task"]
    except asyncio.CancelledError:
        pass
    try:
        await app["queue_processor_task"]
    except asyncio.CancelledError:
        pass
