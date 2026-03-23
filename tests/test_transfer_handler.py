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
