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
    bot.joined_channels = {"#room": 1.0}
    bot.bot_channel_map = {"sender": {"#room"}}
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", mock_open()):
        handler.on_dccmsg(dcc, event)

    assert bot.joined_channels["#room"] > 1.0
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

    with patch("builtins.open", mock_open()) as mocked_open:
        handler.on_dccmsg(dcc, event)

    assert transfer["status"] == "error"
    assert "Invalid MIME type" in transfer["error"]
    dcc.disconnect.assert_called_once()
    # A rejected chunk must not be written or acknowledged.
    mocked_open().write.assert_not_called()
    dcc.send_bytes.assert_not_called()


def test_on_dccmsg_mime_check_exception_aborts_without_write():
    """A crashing MIME checker aborts the transfer without writing the chunk."""
    dcc = MagicMock()
    transfer = _make_transfer(size=1024)
    bot = _make_bot_with_transfer(dcc, transfer)
    bot.allowed_mimetypes = ["video/mp4"]
    bot.mime_checker.from_buffer.side_effect = RuntimeError("parser boom")
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", mock_open()) as mocked_open:
        handler.on_dccmsg(dcc, event)

    assert transfer["status"] == "error"
    assert "MIME check failed" in transfer["error"]
    dcc.disconnect.assert_called_once()
    mocked_open().write.assert_not_called()
    dcc.send_bytes.assert_not_called()


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


def test_on_dccmsg_skips_mime_check_for_completed_transfer():
    """Test transfer handler does not MIME-check already-completed transfers."""
    dcc = MagicMock()
    transfer = _make_transfer(size=1024)
    transfer["completed"] = True
    bot = _make_bot_with_transfer(dcc, transfer)
    bot.allowed_mimetypes = ["video/mp4"]
    bot.mime_checker.from_buffer.return_value = "text/plain"
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", mock_open()):
        handler.on_dccmsg(dcc, event)

    bot.mime_checker.from_buffer.assert_not_called()
    dcc.disconnect.assert_not_called()
    dcc.send_bytes.assert_called_once()


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


def test_on_dcc_disconnect_preserves_cancelled_status(tmp_path):
    """Test disconnect does not overwrite an already cancelled transfer."""
    dcc = MagicMock()
    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"x" * 4)
    transfer = _make_transfer(size=4)
    transfer["bytes_received"] = 4
    transfer["file_path"] = str(file_path)
    transfer["status"] = "cancelled"
    transfer["error"] = "Cancelled by user"
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    handler.on_dcc_disconnect(dcc, event)
    assert transfer["status"] == "cancelled"
    assert transfer["error"] == "Cancelled by user"
    assert dcc not in bot.current_transfers


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
    bot.joined_channels = {"#room": 1.0}
    bot.bot_channel_map = {"sender": {"#room"}}
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    before = bot.joined_channels["#room"]
    handler.on_dcc_disconnect(dcc, event)
    assert bot.joined_channels["#room"] > before


def test_touch_channel_activity_does_not_create_phantom_channels():
    """Activity updates should not add channels the bot has actually left."""
    dcc = MagicMock()
    bot = MagicMock()
    bot.joined_channels = {"#other": 1.0}
    bot.bot_channel_map = {"sender": {"#room", "#other"}}
    transfer = {"nick": "Sender"}
    handler = TransferHandler(bot)

    before = bot.joined_channels["#other"]
    handler._touch_channel_activity(transfer)
    assert bot.joined_channels["#other"] > before
    assert "#room" not in bot.joined_channels


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

    with patch("os.rename", side_effect=OSError("rename failed")), patch("asyncio.create_task") as mock_create_task:
        handler.on_dcc_disconnect(dcc, event)

    mock_create_task.assert_called_once()
    assert transfer["status"] == "completed"


def test_on_dccmsg_marks_connected_and_in_progress():
    """on_dccmsg should mark the transfer connected and in_progress."""
    dcc = MagicMock()
    transfer = _make_transfer(size=1024)
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", mock_open()):
        handler.on_dccmsg(dcc, event)

    assert transfer["connected"] is True
    assert transfer["status"] == "in_progress"


def test_on_dccmsg_exact_remaining_chunk_is_accepted():
    """A chunk exactly filling the declared size must not abort."""
    dcc = MagicMock()
    transfer = _make_transfer(size=3)
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", mock_open()):
        handler.on_dccmsg(dcc, event)

    dcc.disconnect.assert_not_called()
    assert transfer["bytes_received"] == 3


def test_on_dccmsg_accumulates_bytes_across_chunks():
    """bytes_received accumulates across multiple chunks."""
    dcc = MagicMock()
    transfer = _make_transfer(size=10)
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)

    with patch("builtins.open", mock_open()):
        for chunk in (b"abc", b"def"):
            event = MagicMock()
            event.arguments = [chunk]
            handler.on_dccmsg(dcc, event)

    assert transfer["bytes_received"] == 6
    dcc.send_bytes.assert_called_with(struct.pack("!I", 6))


def test_on_dccmsg_passes_chunk_data_to_mime_check():
    """The first chunk's bytes must be handed to the MIME checker."""
    dcc = MagicMock()
    transfer = _make_transfer(size=1024)
    bot = _make_bot_with_transfer(dcc, transfer)
    bot.allowed_mimetypes = ["application/octet-stream"]
    bot.mime_checker.from_buffer.return_value = "application/octet-stream"
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", mock_open()):
        handler.on_dccmsg(dcc, event)

    bot.mime_checker.from_buffer.assert_called_once_with(b"abc")
    dcc.disconnect.assert_not_called()


def test_mime_check_skipped_on_resume():
    """A resumed transfer (offset>0, fresh bytes=0) skips the MIME check."""
    dcc = MagicMock()
    transfer = _make_transfer(size=1024, offset=500)
    bot = _make_bot_with_transfer(dcc, transfer)
    bot.allowed_mimetypes = ["application/octet-stream"]
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", mock_open()):
        handler.on_dccmsg(dcc, event)

    bot.mime_checker.from_buffer.assert_not_called()
    dcc.send_bytes.assert_called_once()


def test_mime_check_runs_only_on_first_chunk():
    """MIME check must not re-run on subsequent chunks."""
    dcc = MagicMock()
    transfer = _make_transfer(size=1024)
    bot = _make_bot_with_transfer(dcc, transfer)
    bot.allowed_mimetypes = ["application/octet-stream"]
    bot.mime_checker.from_buffer.return_value = "application/octet-stream"
    handler = TransferHandler(bot)

    with patch("builtins.open", mock_open()):
        for chunk in (b"abc", b"def"):
            event = MagicMock()
            event.arguments = [chunk]
            handler.on_dccmsg(dcc, event)

    bot.mime_checker.from_buffer.assert_called_once()


def test_send_ack_includes_offset():
    """The ACK must acknowledge offset + bytes_received, not just received."""
    dcc = MagicMock()
    transfer = _make_transfer(size=1024, offset=100)
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", mock_open()):
        handler.on_dccmsg(dcc, event)

    dcc.send_bytes.assert_called_once_with(struct.pack("!I", 103))


def test_send_ack_32bit_unsigned_at_high_offset():
    """A 32-bit ACK above 2**31 must not overflow signed packing."""
    dcc = MagicMock()
    transfer = _make_transfer(size=3 * 1024 * 1024 * 1024, offset=2 * 1024 * 1024 * 1024)
    transfer["bytes_received"] = 600 * 1024 * 1024
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", mock_open()):
        handler.on_dccmsg(dcc, event)

    dcc.send_bytes.assert_called_once_with(struct.pack("!I", 2 * 1024 * 1024 * 1024 + 600 * 1024 * 1024 + 3))


def test_on_dcc_disconnect_clears_connected(tmp_path):
    """Disconnect should mark the transfer as no longer connected."""
    dcc = MagicMock()
    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"x" * 4)
    transfer = _make_transfer(size=4)
    transfer["bytes_received"] = 4
    transfer["file_path"] = str(file_path)
    transfer["connected"] = True
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    handler.on_dcc_disconnect(dcc, event)
    assert transfer["connected"] is False


def test_abort_transfer_without_registration_does_not_raise():
    """Aborting an unregistered connection must not raise KeyError."""
    dcc = MagicMock()
    bot = MagicMock()
    bot.current_transfers = {}
    handler = TransferHandler(bot)
    transfer = _make_transfer()
    transfer["connected"] = True

    handler._abort_transfer(dcc, transfer, "boom", "failed")
    assert transfer["status"] == "failed"
    assert transfer["connected"] is False


def test_finalize_cancelled_transfer_without_registration(tmp_path):
    """Finalizing a cancelled transfer whose dcc is gone must not raise."""
    dcc = MagicMock()
    bot = MagicMock()
    bot.current_transfers = {}
    handler = TransferHandler(bot)
    transfer = _make_transfer()
    transfer["status"] = "cancelled"

    handler._finalize_transfer(dcc, transfer)
    assert transfer["status"] == "cancelled"


def test_finalize_completed_transfer_without_registration(tmp_path):
    """Finalizing a completed transfer whose dcc is gone must not raise."""
    dcc = MagicMock()
    bot = MagicMock()
    bot.current_transfers = {}
    bot.config = {}
    handler = TransferHandler(bot)
    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"x" * 4)
    transfer = _make_transfer(size=4)
    transfer["bytes_received"] = 4
    transfer["file_path"] = str(file_path)

    handler._finalize_transfer(dcc, transfer)
    assert transfer["status"] == "completed"


def test_mark_failure_preserves_existing_error():
    """_mark_failure must not overwrite an already-errored transfer."""
    bot = MagicMock()
    handler = TransferHandler(bot)
    transfer = _make_transfer()
    transfer["status"] = "error"
    transfer["error"] = "original error"

    handler._mark_failure(transfer, "failed", "new error")
    assert transfer["status"] == "error"
    assert transfer["error"] == "original error"


def test_write_failure_does_not_ack():
    """After a write failure the handler must not send an ACK."""
    dcc = MagicMock()
    transfer = _make_transfer(size=1024)
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = [b"abc"]

    with patch("builtins.open", side_effect=OSError("disk full")):
        handler.on_dccmsg(dcc, event)

    dcc.send_bytes.assert_not_called()
    assert transfer["bytes_received"] == 0


def test_finalize_stat_failure_reports_error(tmp_path):
    """A getsize OSError during finalize marks the transfer errored."""
    dcc = MagicMock()
    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"x" * 4)
    transfer = _make_transfer(size=4)
    transfer["bytes_received"] = 4
    transfer["file_path"] = str(file_path)
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    with patch("os.path.getsize", side_effect=OSError("gone")):
        handler.on_dcc_disconnect(dcc, event)

    assert transfer["status"] == "error"
    assert "Cannot stat" in transfer["error"]


def test_finalize_missing_file_error_contains_path():
    """Missing-file error message should include the file path."""
    dcc = MagicMock()
    transfer = _make_transfer(size=4)
    transfer["file_path"] = "/tmp/definitely-missing-file.bin"
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    with patch("os.path.exists", return_value=False):
        handler.on_dcc_disconnect(dcc, event)

    assert "/tmp/definitely-missing-file.bin" in transfer["error"]


def test_update_progress_skips_zero_size():
    """size=0 transfers must return early without dividing by zero."""
    bot = MagicMock()
    handler = TransferHandler(bot)
    transfer = _make_transfer(size=0)
    handler._update_progress(transfer)
    assert transfer["percent"] == 0


def test_update_progress_size_one_still_updates():
    """A size=1 transfer is still valid and must update progress."""
    bot = MagicMock()
    handler = TransferHandler(bot)
    transfer = _make_transfer(size=1)
    transfer["bytes_received"] = 1
    transfer["last_progress_update"] = 0.0

    with patch("dccbot.transfer_handler.time.time", return_value=100.0):
        handler._update_progress(transfer)
    assert transfer["percent"] == 100


def test_update_progress_full_percent_is_100():
    """Complete progress reports exactly 100%."""
    bot = MagicMock()
    handler = TransferHandler(bot)
    transfer = _make_transfer(size=100)
    transfer["bytes_received"] = 100
    transfer["last_progress_update"] = 0.0

    with patch("dccbot.transfer_handler.time.time", return_value=100.0):
        handler._update_progress(transfer)
    assert transfer["percent"] == 100


def test_update_progress_exactly_ten_percent_updates():
    """A jump of exactly 10 percent must trigger an update."""
    bot = MagicMock()
    handler = TransferHandler(bot)
    transfer = _make_transfer(size=1000)
    transfer["bytes_received"] = 100  # exactly +10%
    transfer["last_progress_update"] = 50.0

    with patch("dccbot.transfer_handler.time.time", return_value=51.0):
        handler._update_progress(transfer)
    assert transfer["percent"] == 10
    assert transfer["last_progress_update"] == 51.0


def test_update_progress_exactly_five_seconds_updates():
    """A small jump updates once the last update was exactly 5s ago."""
    bot = MagicMock()
    handler = TransferHandler(bot)
    transfer = _make_transfer(size=1000)
    transfer["bytes_received"] = 50  # 5% jump < 10%
    transfer["last_progress_update"] = 50.0

    with patch("dccbot.transfer_handler.time.time", return_value=55.0):
        handler._update_progress(transfer)
    assert transfer["percent"] == 5
    assert transfer["last_progress_update"] == 55.0
    assert transfer["last_progress_bytes_received"] == 50


def test_update_progress_computes_percent():
    """Progress percent reflects bytes_received + offset over size."""
    bot = MagicMock()
    handler = TransferHandler(bot)
    transfer = _make_transfer(size=200, offset=50)
    transfer["bytes_received"] = 50
    transfer["last_progress_update"] = time.time() - 60

    handler._update_progress(transfer)
    assert transfer["percent"] == 50


def test_update_progress_throttles_small_jumps():
    """Small progress deltas within 5s must not trigger an update."""
    bot = MagicMock()
    handler = TransferHandler(bot)
    transfer = _make_transfer(size=1000)
    transfer["bytes_received"] = 50  # 5% jump < 10%
    transfer["last_progress_update"] = time.time() - 1
    before = transfer["last_progress_update"]

    handler._update_progress(transfer)
    assert transfer["last_progress_update"] == before
    assert transfer["percent"] == 0


def test_update_progress_updates_on_large_jump():
    """A >=10% jump triggers an update even within 5s."""
    bot = MagicMock()
    handler = TransferHandler(bot)
    transfer = _make_transfer(size=1000)
    transfer["bytes_received"] = 150  # 15% jump
    transfer["last_progress_update"] = time.time() - 1

    handler._update_progress(transfer)
    assert transfer["percent"] == 15
    assert transfer["last_progress_bytes_received"] == 150
    assert transfer["last_progress_update"] > time.time() - 5


def test_update_progress_updates_after_5s():
    """Progress updates after 5s even with a small percent jump."""
    bot = MagicMock()
    handler = TransferHandler(bot)
    transfer = _make_transfer(size=1000)
    transfer["bytes_received"] = 50  # 5% jump < 10%
    transfer["last_progress_update"] = time.time() - 10

    handler._update_progress(transfer)
    assert transfer["percent"] == 5
    assert transfer["last_progress_bytes_received"] == 50


def test_mark_complete_enqueues_md5_with_transfer(tmp_path):
    """MD5 queueing receives the transfer dict, and a real coroutine."""
    dcc = MagicMock()
    file_path = tmp_path / "file.bin"
    file_path.write_bytes(b"x" * 4)
    transfer = _make_transfer(size=4)
    transfer["bytes_received"] = 4
    transfer["file_path"] = str(file_path)
    transfer["md5"] = "deadbeef"
    bot = _make_bot_with_transfer(dcc, transfer)
    handler = TransferHandler(bot)
    event = MagicMock()
    event.arguments = []

    with patch("asyncio.create_task") as mock_create_task:
        handler.on_dcc_disconnect(dcc, event)

    bot._add_md5_check_queue_item.assert_called_once_with(transfer)
    mock_create_task.assert_called_once_with(bot._add_md5_check_queue_item.return_value)
