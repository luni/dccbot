"""Transfer record helpers and normalization utilities."""

from __future__ import annotations

import time
import uuid
from typing import Any

TRANSFER_STATUSES = {"started", "in_progress", "completed", "failed", "error", "cancelled"}

# Static defaults shared by all transfer records. Dynamic defaults (id,
# timestamps, filename, status) are filled in by the helpers below.
_STATIC_DEFAULTS: dict[str, Any] = {
    "nick": "",
    "server": "",
    "peer_address": None,
    "peer_port": None,
    "file_path": None,
    "bytes_received": 0,
    "offset": 0,
    "size": 0,
    "ssl": False,
    "percent": 0,
    "completed": False,
    "connected": False,
    "md5": None,
    "file_md5": None,
    "error": None,
}


def get_incomplete_suffix(config: dict[str, Any]) -> str | None:
    """Return the configured incomplete file suffix if it is a non-empty string."""
    suffix = config.get("incomplete_suffix")
    return suffix if isinstance(suffix, str) and suffix else None


def normalize_status(transfer: dict[str, Any]) -> str:
    """Return a valid transfer status based on the current transfer data."""
    status = transfer.get("status")
    if status in TRANSFER_STATUSES:
        return status

    if transfer.get("error"):
        return "error"
    completed = transfer.get("completed")
    if isinstance(completed, (int, float)) and not isinstance(completed, bool) and completed > 0:
        return "completed"
    if transfer.get("connected") or transfer.get("bytes_received", 0) > 0:
        return "in_progress"
    return "started"


def _base_transfer(filename: str, ts: float) -> dict[str, Any]:
    """Return a transfer record with all defaults applied."""
    return {
        "id": uuid.uuid4().hex,
        "filename": filename,
        "start_time": ts,
        **_STATIC_DEFAULTS,
        "last_progress_update": ts,
        "last_progress_bytes_received": 0,
        "status": "started",
    }


def create_pending_transfer(filename: str, nick: str | None, server: str, *, md5: str | None = None, now: float | None = None) -> dict[str, Any]:
    """Create a normalized transfer entry from a pre-transfer XDCC message."""
    transfer = _base_transfer(filename, now if now is not None else time.time())
    transfer.update({"nick": nick, "server": server, "md5": md5})
    return transfer


def create_transfer(
    *,
    nick: str,
    server: str,
    peer_address: str,
    peer_port: int,
    file_path: str,
    filename: str,
    size: int,
    offset: int = 0,
    use_ssl: bool = False,
    completed: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Create a normalized transfer entry for an active DCC transfer."""
    transfer = _base_transfer(filename, now if now is not None else time.time())
    transfer.update({
        "nick": nick,
        "server": server,
        "peer_address": peer_address,
        "peer_port": peer_port,
        "file_path": file_path,
        "offset": offset,
        "size": size,
        "ssl": use_ssl,
        "completed": completed,
    })
    return transfer


def ensure_transfer_defaults(filename: str, transfer: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """Patch transfer dictionary in-place with required defaults."""
    ts = now if now is not None else time.time()
    transfer.setdefault("id", uuid.uuid4().hex)
    transfer.setdefault("filename", filename)
    transfer.setdefault("start_time", ts)
    for key, value in _STATIC_DEFAULTS.items():
        transfer.setdefault(key, value)
    transfer.setdefault("last_progress_update", transfer["start_time"])
    transfer.setdefault("last_progress_bytes_received", transfer["bytes_received"])
    transfer["status"] = normalize_status(transfer)
    return transfer


def transfer_speeds(transfer: dict[str, Any], now: float) -> tuple[float, float]:
    """Return (current, average) transfer speed in KB/s for the given timestamp."""
    transfer_time = now - transfer["start_time"] if transfer["start_time"] else 0
    speed_avg = transfer["bytes_received"] / transfer_time / 1024 if transfer_time > 0 else 0

    recent_bytes = transfer["bytes_received"] - transfer["last_progress_bytes_received"]
    recent_duration = now - transfer["last_progress_update"]
    speed = (recent_bytes / recent_duration) / 1024 if recent_duration > 0 else 0
    return speed, speed_avg
