"""Tests for transfer normalization helpers."""

import pytest
from unittest.mock import patch

from dccbot.transfers import (
    create_pending_transfer,
    create_transfer,
    ensure_transfer_defaults,
    normalize_status,
    transfer_speeds,
)


def test_normalize_status_prefers_explicit_status():
    """Explicit valid status should be returned unchanged."""
    assert normalize_status({"status": "cancelled"}) == "cancelled"


def test_normalize_status_derived_branches():
    """Derived status should map from error/completed/progress/default."""
    assert normalize_status({"error": "boom"}) == "error"
    assert normalize_status({"completed": 123}) == "completed"
    assert normalize_status({"completed": True}) == "started"
    assert normalize_status({"completed": True, "connected": True}) == "in_progress"
    assert normalize_status({"connected": True}) == "in_progress"
    assert normalize_status({"bytes_received": 1}) == "in_progress"
    assert normalize_status({"completed": 0}) == "started"
    assert normalize_status({}) == "started"


def test_create_pending_transfer_uses_current_time_when_now_missing():
    """Pending transfer should default timestamps from time.time()."""
    with patch("dccbot.transfers.time.time", return_value=42.0):
        transfer = create_pending_transfer("file.bin", "nick", "server")

    assert transfer["start_time"] == 42.0
    assert transfer["last_progress_update"] == 42.0
    assert transfer["last_progress_bytes_received"] == 0
    assert transfer["status"] == "started"


def test_create_pending_transfer_uses_passed_now():
    """An explicit now must be used for the pending transfer timestamps."""
    transfer = create_pending_transfer("file.bin", "nick", "server", now=7.5)

    assert transfer["start_time"] == 7.5
    assert transfer["last_progress_update"] == 7.5
    assert transfer["last_progress_bytes_received"] == 0


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
    assert transfer["last_progress_bytes_received"] == 0
    assert transfer["offset"] == 0
    assert transfer["status"] == "started"


def test_create_transfer_fields():
    """Active transfer should carry the passed fields verbatim."""
    transfer = create_transfer(
        nick="nick",
        server="server",
        peer_address="1.2.3.4",
        peer_port=5000,
        file_path="/tmp/file.bin",
        filename="file.bin",
        size=100,
        offset=10,
        now=84.0,
    )

    assert transfer["start_time"] == 84.0
    assert transfer["nick"] == "nick"
    assert transfer["server"] == "server"
    assert transfer["peer_address"] == "1.2.3.4"
    assert transfer["peer_port"] == 5000
    assert transfer["file_path"] == "/tmp/file.bin"
    assert transfer["offset"] == 10
    assert transfer["size"] == 100
    assert transfer["ssl"] is False
    assert transfer["completed"] is False
    assert isinstance(transfer["id"], str) and transfer["id"]


def test_create_transfer_completed_true():
    """A resumed/completed create_transfer should keep completed=True."""
    transfer = create_transfer(
        nick="nick",
        server="server",
        peer_address="1.2.3.4",
        peer_port=5000,
        file_path="/tmp/file.bin",
        filename="file.bin",
        size=100,
        completed=True,
        now=84.0,
    )
    assert transfer["completed"] is True


def test_ensure_transfer_defaults_fills_and_normalizes():
    """ensure_transfer_defaults should fill missing fields and derive status."""
    transfer = {"bytes_received": 10, "status": "unexpected"}
    ensure_transfer_defaults("movie.mkv", transfer, now=5.0)
    assert transfer["filename"] == "movie.mkv"
    assert transfer["start_time"] == 5.0
    assert transfer["status"] == "in_progress"


def test_ensure_transfer_defaults_without_now_uses_current_time():
    """ensure_transfer_defaults should fall back to time.time() when now is None."""
    with patch("dccbot.transfers.time.time", return_value=99.0):
        transfer = {"bytes_received": 10}
        ensure_transfer_defaults("movie.mkv", transfer)

    assert transfer["start_time"] == 99.0
    assert transfer["last_progress_update"] == 99.0
    assert isinstance(transfer["id"], str) and transfer["id"]


@pytest.mark.parametrize(
    "transfer,now,expected_speed,expected_avg",
    [
        # 10s elapsed, 102400 bytes total → 10 KB/s avg; 2048 bytes in last 2s → 1 KB/s current
        (
            {"start_time": 10.0, "bytes_received": 102400, "last_progress_update": 18.0, "last_progress_bytes_received": 100352},
            20.0,
            1.0,
            10.0,
        ),
        # Falsy start_time → zero avg; current speed still computed from the progress window
        (
            {"start_time": 0, "bytes_received": 102400, "last_progress_update": 0.0, "last_progress_bytes_received": 0},
            10.0,
            10.0,
            0,
        ),
        # No progress window elapsed → current speed 0, avg still computed
        (
            {"start_time": 10.0, "bytes_received": 102400, "last_progress_update": 20.0, "last_progress_bytes_received": 102400},
            20.0,
            0,
            10.0,
        ),
        # Sub-second windows still count: 0.5s total, 0.25s window
        (
            {"start_time": 10.0, "bytes_received": 102400, "last_progress_update": 10.25, "last_progress_bytes_received": 100352},
            10.5,
            8.0,
            200.0,
        ),
    ],
)
def test_transfer_speeds_values(transfer, now, expected_speed, expected_avg):
    """transfer_speeds should return exact (current, average) KB/s values."""
    speed, avg = transfer_speeds(transfer, now)
    assert speed == pytest.approx(expected_speed)
    assert avg == pytest.approx(expected_avg)
