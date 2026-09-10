"""Parsing and validation helpers for DCC commands."""

from __future__ import annotations

import ipaddress
import os
import re
import shlex
from dataclasses import dataclass

import irc.client


@dataclass(frozen=True)
class DccSendPayload:
    """Parsed payload from a DCC SEND/SSEND message."""

    filename: str
    peer_address: str
    peer_port: int
    size: int
    token: int | None = None


@dataclass(frozen=True)
class DccAcceptPayload:
    """Parsed payload from a DCC ACCEPT message."""

    port: int
    position: int
    token: int | None = None


def _parse_int(value: str, min_value: int = 0, max_value: int | None = None) -> int | None:
    """Parse an integer, returning None if out of range or not numeric."""
    try:
        result = int(value)
    except ValueError:
        return None
    if result < min_value or (max_value is not None and result > max_value):
        return None
    return result


def is_valid_filename(path: str, filename: str) -> bool:
    """Check if filename is valid and resolves within the given base path."""
    if not filename:
        return False

    file_path = os.path.join(path, filename)
    if re.search(r"[/\\:\*?\"<>\|]", filename):
        return False

    abs_file_path = os.path.abspath(file_path)
    abs_base_path = os.path.abspath(path)
    try:
        if os.path.commonpath([abs_file_path, abs_base_path]) != abs_base_path:
            return False
    except ValueError:
        return False

    return True


def parse_dcc_accept(payload: str) -> DccAcceptPayload | None:
    """Parse DCC ACCEPT payload into structured data."""
    try:
        parts = shlex.split(payload)
    except ValueError:
        return None
    if len(parts) < 4:
        return None

    port = _parse_int(parts[2], 0, 65535)
    position = _parse_int(parts[3], 0)
    if port is None or position is None:
        return None

    token: int | None = None
    if len(parts) == 5:
        token = _parse_int(parts[4], 0)
        if token is None:
            return None
    elif len(parts) > 5:
        return None

    return DccAcceptPayload(port=port, position=position, token=token)


def parse_dcc_send(payload: str) -> DccSendPayload | None:
    """Parse DCC SEND/SSEND payload into structured data."""
    try:
        parts = shlex.split(payload)
    except ValueError:
        return None
    if len(parts) < 5 or len(parts) > 6:
        return None

    filename, raw_address, raw_port, raw_size = parts[1:5]
    peer_port = _parse_int(raw_port, 0, 65535)
    size = _parse_int(raw_size, 1)
    if peer_port is None or size is None:
        return None

    token: int | None = None
    if len(parts) == 6:
        token = _parse_int(parts[5], 0)
        if token is None:
            return None

    try:
        if "." in raw_address or ":" in raw_address:
            ipaddress.ip_address(raw_address)
            peer_address = raw_address
        else:
            peer_address = irc.client.ip_numstr_to_quad(raw_address)
    except ValueError:
        return None

    return DccSendPayload(filename=filename, peer_address=peer_address, peer_port=peer_port, size=size, token=token)
