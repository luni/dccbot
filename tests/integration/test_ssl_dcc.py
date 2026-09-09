"""Integration tests for Secure DCC (SDCC / SSEND)."""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


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
    dcc = None
    for _ in range(50):
        if bot.current_transfers:
            dcc = list(bot.current_transfers.keys())[0]
            if getattr(dcc, "localport", None):
                break
        await asyncio.sleep(0.05)

    assert dcc is not None, "passive listener was not created"
    assert dcc.localport is not None
    assert dcc.localaddress is not None

    transfer = list(bot.current_transfers.values())[0]

    # Connect as a TLS client. DCC/SDCC does not verify certificates.
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE

    reader, writer = await asyncio.open_connection(dcc.localaddress, dcc.localport, ssl=client_ctx)
    writer.write(b"hello world")
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

    # Wait for the transfer to finalize.
    for _ in range(50):
        if transfer.get("status") in ("completed", "error"):
            break
        await asyncio.sleep(0.05)

    assert transfer["ssl"] is True
    assert transfer["filename"] == "hello.txt"
    assert transfer["bytes_received"] == 11
    assert transfer["status"] == "completed"

    file_path = Path(transfer["file_path"])
    assert file_path.read_bytes() == b"hello world"
