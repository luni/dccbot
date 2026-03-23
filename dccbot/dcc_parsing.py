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


def is_valid_filename(path: str, filename: str) -> bool:
    """Check if filename is valid and resolves within the given base path."""
    if not filename:
        return False

    file_path = os.path.join(path, filename)
    if re.search(r"[/\\:\*?\"<>\|]", filename):
        return False

    if not os.path.abspath(file_path).startswith(path):
        return False

    return True


def parse_dcc_accept(payload: str) -> tuple[int, int] | None:
    """Parse DCC ACCEPT payload into (peer_port, resume_position)."""
    match = re.search(r"(\d+) (\d+)$", payload)
    if not match:
        return None

    peer_port = int(match.group(1))
    resume_position = int(match.group(2))
    if peer_port < 1024 or peer_port > 65535 or resume_position < 1:
        return None
    return peer_port, resume_position


def parse_dcc_send(payload: str) -> DccSendPayload | None:
    """Parse DCC SEND/SSEND payload into structured data."""
    parts = shlex.split(payload)
    if len(parts) < 5:
        return None

    filename, raw_address, raw_port, raw_size = parts[1:5]
    try:
        size = int(raw_size)
        peer_port = int(raw_port)
    except ValueError:
        return None

    if peer_port < 0 or peer_port > 65535 or size < 1:
        return None

    if ":" in raw_address:
        try:
            ipaddress.ip_address(raw_address)
            peer_address = raw_address
        except ValueError:
            return None
    else:
        try:
            peer_address = irc.client.ip_numstr_to_quad(raw_address)
        except ValueError:
            return None

    return DccSendPayload(filename=filename, peer_address=peer_address, peer_port=peer_port, size=size)
