"""Integration tests for Secure DCC (SDCC / SSEND)."""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


def _ready_listener(bot) -> Any | None:
    """Return the passive DCC listener once it has a bound port."""
    if not bot.current_transfers:
        return None
    dcc = next(iter(bot.current_transfers))
    return dcc if getattr(dcc, "localport", None) else None


async def _poll(predicate, attempts: int = 50) -> Any | None:
    """Poll ``predicate`` until it returns a truthy value."""
    for _ in range(attempts):
        result = predicate()
        if result:
            return result
        await asyncio.sleep(0.05)
    return None


async def _send_and_close(dcc, payload: bytes) -> None:
    """Connect to the listener over TLS, send ``payload``, and close."""
    # Connect as a TLS client. DCC/SDCC does not verify certificates.
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE

    reader, writer = await asyncio.open_connection(dcc.localaddress, dcc.localport, ssl=client_ctx)
    writer.write(payload)
    await writer.drain()
    # DCC SEND sends a 4-byte cumulative ACK. Read it before closing to avoid
    # a TLS close_notify race where the ACK arrives after shutdown starts.
    try:
        await asyncio.wait_for(reader.read(4), timeout=0.5)
    except asyncio.TimeoutError:
        pass
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
    except (asyncio.TimeoutError, ssl.SSLError):
        pass


@pytest.mark.integration
@pytest.mark.asyncio
async def test_passive_ssend_download(irc_bot_factory, irc_bot_manager, tmp_path):
    """End-to-end passive SDCC: bot listens on TLS, peer connects and sends data."""
    bot = irc_bot_factory()
    loop = asyncio.get_event_loop()
    bot.loop = loop
    bot.reactor.loop = loop
    bot.connection = MagicMock()
    bot.server_config["passive_dcc"] = True
    bot.server_config["passive_dcc_listen_ip"] = "127.0.0.1"
    bot.server_config["passive_dcc_port_range"] = [25000, 25100]
    bot.server_config["passive_dcc_timeout"] = 10
    irc_bot_manager._cert_cache_dir = tmp_path

    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = "sender"
    event.arguments = ["DCC", 'SSEND "hello.txt" 0 0 11 99']

    bot.on_dcc_send(bot.connection, event, True)

    # Wait for the TLS listener to be ready.
    dcc = await _poll(lambda: _ready_listener(bot))

    assert dcc is not None, "passive listener was not created"
    assert dcc.localport is not None
    assert dcc.localaddress is not None

    transfer = list(bot.current_transfers.values())[0]

    await _send_and_close(dcc, b"hello world")

    # Wait for the transfer to finalize.
    await _poll(lambda: transfer.get("status") in ("completed", "error"))

    assert transfer["ssl"] is True
    assert transfer["filename"] == "hello.txt"
    assert transfer["bytes_received"] == 11
    assert transfer["status"] == "completed"

    file_path = Path(transfer["file_path"])
    assert file_path.read_bytes() == b"hello world"
