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

    try:
        port = int(parts[2])
        position = int(parts[3])
    except ValueError:
        return None
    if port < 0 or port > 65535 or position < 0:
        return None

    token: int | None = None
    if len(parts) == 5:
        try:
            token = int(parts[4])
        except ValueError:
            return None
        if token < 0:
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
    try:
        size = int(raw_size)
        peer_port = int(raw_port)
    except ValueError:
        return None

    if peer_port < 0 or peer_port > 65535 or size < 1:
        return None

    token: int | None = None
    if len(parts) == 6:
        try:
            token = int(parts[5])
        except ValueError:
            return None
        if token < 0:
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
