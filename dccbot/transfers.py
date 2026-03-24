"""Transfer record helpers and normalization utilities."""

from __future__ import annotations

import time
import uuid
from typing import Any

TRANSFER_STATUSES = {"started", "in_progress", "completed", "failed", "error", "cancelled"}


def normalize_status(transfer: dict[str, Any]) -> str:
    """Return a valid transfer status based on the current transfer data."""
    status = transfer.get("status")
    if status in TRANSFER_STATUSES:
        return status

    if transfer.get("error"):
        return "error"
    if transfer.get("completed"):
        return "completed"
    if transfer.get("connected") or transfer.get("bytes_received", 0) > 0:
        return "in_progress"
    return "started"


def create_pending_transfer(filename: str, nick: str | None, server: str, *, md5: str | None = None, now: float | None = None) -> dict[str, Any]:
    """Create a normalized transfer entry from a pre-transfer XDCC message."""
    ts = now if now is not None else time.time()
    return {
        "id": uuid.uuid4().hex,
        "filename": filename,
        "nick": nick,
        "server": server,
        "peer_address": None,
        "peer_port": None,
        "file_path": None,
        "start_time": ts,
        "bytes_received": 0,
        "offset": 0,
        "size": 0,
        "ssl": False,
        "percent": 0,
        "last_progress_update": ts,
        "last_progress_bytes_received": 0,
        "completed": False,
        "connected": False,
        "md5": md5,
        "file_md5": None,
        "error": None,
        "status": "started",
    }


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
    ts = now if now is not None else time.time()
    return {
        "id": uuid.uuid4().hex,
        "nick": nick,
        "server": server,
        "peer_address": peer_address,
        "peer_port": peer_port,
        "file_path": file_path,
        "filename": filename,
        "start_time": ts,
        "bytes_received": 0,
        "offset": offset,
        "size": size,
        "ssl": use_ssl,
        "percent": 0,
        "last_progress_update": ts,
        "last_progress_bytes_received": 0,
        "completed": completed,
        "connected": False,
        "md5": None,
        "file_md5": None,
        "error": None,
        "status": "started",
    }


def ensure_transfer_defaults(filename: str, transfer: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """Patch transfer dictionary in-place with required defaults."""
    ts = now if now is not None else time.time()
    transfer.setdefault("id", uuid.uuid4().hex)
    transfer.setdefault("filename", filename)
    transfer.setdefault("nick", "")
    transfer.setdefault("server", "")
    transfer.setdefault("peer_address", None)
    transfer.setdefault("peer_port", None)
    transfer.setdefault("file_path", None)
    transfer.setdefault("start_time", ts)
    transfer.setdefault("bytes_received", 0)
    transfer.setdefault("offset", 0)
    transfer.setdefault("size", 0)
    transfer.setdefault("ssl", False)
    transfer.setdefault("percent", 0)
    transfer.setdefault("last_progress_update", transfer["start_time"])
    transfer.setdefault("last_progress_bytes_received", transfer["bytes_received"])
    transfer.setdefault("completed", False)
    transfer.setdefault("connected", False)
    transfer.setdefault("md5", None)
    transfer.setdefault("file_md5", None)
    transfer.setdefault("error", None)
    transfer["status"] = normalize_status(transfer)
    return transfer
