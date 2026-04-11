"""Unit tests for transfer handler behavior."""

import asyncio
import struct
import time
from unittest.mock import MagicMock, mock_open, patch

import pytest

from dccbot.transfer_handler import TransferHandler


def _make_transfer(*, size: int = 10, offset: int = 0) -> dict:
    return {
        "nick": "sender",
        "filename": "file.bin",
        "file_path": "/tmp/file.bin",
        "start_time": time.time() - 1,
        "bytes_received": 0,
        "offset": offset,
        "size": size,
        "percent": 0,
        "last_progress_update": time.time() - 1,
        "last_progress_bytes_received": 0,
        "completed": False,
        "status": "started",
        "connected": False,
    }


def _make_bot_with_transfer(dcc: MagicMock, transfer: dict) -> MagicMock:
    bot = MagicMock()
    bot.current_transfers = {dcc: transfer}
    bot.bot_channel_map = {}
    bot.joined_channels = {}
    bot.allowed_mimetypes = None
    bot.mime_checker = MagicMock()
    bot.config = {}
    bot._add_md5_check_queue_item = MagicMock()
    return bot


def test_on_dccmsg_sends_32bit_ack():
    """Test transfer handler sends 32-bit ACK for regular file sizes."""
    dcc = MagicMock()
    transfer = _make_transfer(size=1024)
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", mock_open()):
        handler.on_dccmsg(dcc, event)

    dcc.send_bytes.assert_called_once_with(struct.pack("!I", 3))


def test_on_dccmsg_unknown_connection():
    """Test unknown DCC connections are ignored safely."""
    dcc = MagicMock()
    bot = MagicMock()
    bot.current_transfers = {}
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    handler.on_dccmsg(dcc, event)
    dcc.send_bytes.assert_not_called()


def test_on_dccmsg_updates_channel_activity_and_progress_fields():
    """Test transfer progress updates channel activity and progress markers."""
    dcc = MagicMock()
    transfer = _make_transfer(size=100, offset=0)
    transfer["bytes_received"] = 20
    transfer["last_progress_bytes_received"] = 0
    transfer["last_progress_update"] = time.time() - 10
    transfer["percent"] = 0
    bot = _make_bot_with_transfer(dcc, transfer)
    bot.bot_channel_map = {"sender": {"#room"}}
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", mock_open()):
        handler.on_dccmsg(dcc, event)

    assert "#room" in bot.joined_channels
    assert transfer["percent"] >= 20
    assert transfer["last_progress_bytes_received"] >= 20


def test_on_dccmsg_sends_64bit_ack():
    """Test transfer handler sends 64-bit ACK for large file sizes."""
    dcc = MagicMock()
    transfer = _make_transfer(size=5 * 1024 * 1024 * 1024)
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", mock_open()):
        handler.on_dccmsg(dcc, event)

    dcc.send_bytes.assert_called_once_with(struct.pack("!Q", 3))


def test_on_dccmsg_rejects_mime_and_disconnects():
    """Test transfer handler rejects invalid MIME on first chunk."""
    dcc = MagicMock()
    transfer = _make_transfer(size=1024)
    bot = _make_bot_with_transfer(dcc, transfer)
    bot.allowed_mimetypes = ["video/mp4"]
    bot.mime_checker.from_buffer.return_value = "text/plain"
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    handler.on_dccmsg(dcc, event)

    assert transfer["status"] == "error"
    assert "Invalid MIME type" in transfer["error"]
    dcc.disconnect.assert_called_once()


def test_on_dccmsg_write_failure_sets_error():
    """Test transfer handler reports write failures."""
    dcc = MagicMock()
    transfer = _make_transfer(size=1024)
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", side_effect=OSError("disk full")):
        handler.on_dccmsg(dcc, event)

    assert transfer["status"] == "error"
    assert "Error writing to file" in transfer["error"]
    dcc.disconnect.assert_called_once()


def test_on_dcc_disconnect_unknown_connection():
    """Test unknown disconnect events are ignored safely."""
    dcc = MagicMock()
    bot = MagicMock()
    bot.current_transfers = {}
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    handler.on_dcc_disconnect(dcc, event)
    dcc.disconnect.assert_not_called()


@pytest.mark.asyncio
async def test_on_dcc_disconnect_completed_marks_status(tmp_path):
    """Test disconnect handling sets completed status for successful transfer."""
    dcc = MagicMock()
    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"x" * 4)
    transfer = _make_transfer(size=4)
    transfer["bytes_received"] = 4
    transfer["file_path"] = str(file_path)
    transfer["md5"] = None
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    with patch.object(asyncio, "get_event_loop") as mock_get_loop:
        mock_get_loop.return_value = MagicMock()
        handler.on_dcc_disconnect(dcc, event)

    assert transfer["status"] == "completed"
    assert transfer.get("completed")


def test_on_dcc_disconnect_missing_file_sets_error():
    """Test missing output files mark transfer as error."""
    dcc = MagicMock()
    transfer = _make_transfer(size=4)
    transfer["file_path"] = "/tmp/definitely-missing-file.bin"
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    with patch("os.path.exists", return_value=False):
        handler.on_dcc_disconnect(dcc, event)

    assert transfer["status"] == "error"
    assert "does not exist" in transfer["error"]


def test_on_dcc_disconnect_size_mismatch_sets_failed(tmp_path):
    """Test size mismatch marks transfer as failed."""
    dcc = MagicMock()
    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"x" * 3)
    transfer = _make_transfer(size=4)
    transfer["bytes_received"] = 3
    transfer["file_path"] = str(file_path)
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    handler.on_dcc_disconnect(dcc, event)
    assert transfer["status"] == "failed"
    assert "size mismatch" in transfer["error"]


def test_on_dcc_disconnect_updates_channel_activity(tmp_path):
    """Test disconnect refreshes channel activity for mapped sender."""
    dcc = MagicMock()
    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"x" * 4)
    transfer = _make_transfer(size=4)
    transfer["bytes_received"] = 4
    transfer["file_path"] = str(file_path)
    bot = _make_bot_with_transfer(dcc, transfer)
    bot.bot_channel_map = {"sender": {"#room"}}
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    handler.on_dcc_disconnect(dcc, event)
    assert "#room" in bot.joined_channels


def test_on_dcc_disconnect_rename_success(tmp_path):
    """Test successful rename when incomplete suffix is configured."""
    dcc = MagicMock()
    file_path = tmp_path / "file.bin.incomplete"
    file_path.write_bytes(b"x" * 4)
    transfer = _make_transfer(size=4)
    transfer["bytes_received"] = 4
    transfer["file_path"] = str(file_path)
    bot = _make_bot_with_transfer(dcc, transfer)
    bot.config = {"incomplete_suffix": ".incomplete"}
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    handler.on_dcc_disconnect(dcc, event)
    assert transfer["file_path"].endswith("file.bin")


def test_on_dcc_disconnect_md5_enqueue_and_rename_error(tmp_path):
    """Test md5 enqueue path and rename error handling on completed transfers."""
    dcc = MagicMock()
    file_path = tmp_path / "file.bin.incomplete"
    file_path.write_bytes(b"x" * 4)
    transfer = _make_transfer(size=4)
    transfer["bytes_received"] = 4
    transfer["file_path"] = str(file_path)
    transfer["md5"] = "abc"
    bot = _make_bot_with_transfer(dcc, transfer)
    bot.config = {"incomplete_suffix": ".incomplete"}
    bot._add_md5_check_queue_item = MagicMock()
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    fake_loop = MagicMock()
    fake_loop.run_until_complete = MagicMock()
    with patch.object(asyncio, "get_event_loop", return_value=fake_loop), patch("os.rename", side_effect=OSError("rename failed")):
        handler.on_dcc_disconnect(dcc, event)

    fake_loop.run_until_complete.assert_called_once()
    assert transfer["status"] == "completed"
