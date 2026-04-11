"""Tests for transfer normalization helpers."""

from unittest.mock import patch

from dccbot.transfers import create_pending_transfer, create_transfer, ensure_transfer_defaults, normalize_status


def test_normalize_status_prefers_explicit_status():
    """Explicit valid status should be returned unchanged."""
    assert normalize_status({"status": "cancelled"}) == "cancelled"


def test_normalize_status_derived_branches():
    """Derived status should map from error/completed/progress/default."""
    assert normalize_status({"error": "boom"}) == "error"
    assert normalize_status({"completed": 123}) == "completed"
    assert normalize_status({"connected": True}) == "in_progress"
    assert normalize_status({"bytes_received": 1}) == "in_progress"
    assert normalize_status({}) == "started"


def test_create_pending_transfer_uses_current_time_when_now_missing():
    """Pending transfer should default timestamps from time.time()."""
    with patch("dccbot.transfers.time.time", return_value=42.0):
        transfer = create_pending_transfer("file.bin", "nick", "server")

    assert transfer["start_time"] == 42.0
    assert transfer["last_progress_update"] == 42.0
    assert transfer["status"] == "started"


def test_create_transfer_uses_current_time_when_now_missing():
    """Active transfer should default timestamps from time.time()."""
    with patch("dccbot.transfers.time.time", return_value=84.0):
        transfer = create_transfer(
            nick="nick",
            server="server",
            peer_address="1.2.3.4",
            peer_port=5000,
            file_path="/tmp/file.bin",
            filename="file.bin",
            size=100,
        )

    assert transfer["start_time"] == 84.0
    assert transfer["last_progress_update"] == 84.0
    assert transfer["status"] == "started"


def test_ensure_transfer_defaults_fills_and_normalizes():
    """ensure_transfer_defaults should fill missing fields and derive status."""
    transfer = {"bytes_received": 10, "status": "unexpected"}
    ensure_transfer_defaults("movie.mkv", transfer, now=5.0)
    assert transfer["filename"] == "movie.mkv"
    assert transfer["start_time"] == 5.0
    assert transfer["status"] == "in_progress"
