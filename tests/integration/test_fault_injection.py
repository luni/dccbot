"""Live fault-injection tests against IRC and DCC paths through toxiproxy.

These tests route real TCP traffic through the toxiproxy sidecar and inject
network-level faults (latency, connection resets, truncation, throttling) to
verify the bot's behavior under real failure conditions.

Requires the toxiproxy sidecar started by ``make irc-up`` /
``make test-integration-local`` and CI.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path

import pytest

from .conftest import ToxicAdder, toxi_request

pytestmark = pytest.mark.integration

TEST_CHANNEL = "#test"
IRC_PROXY_PORT = 16667
DCC_PROXY_PORT = 20001
DCC_PEER_PORT = 20099
FILE_SIZE = 4096
FILE_DATA = os.urandom(FILE_SIZE)

PeerHandler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


async def _wait_for(predicate: Callable[[], bool], timeout: float = 15.0) -> bool:
    """Poll predicate until it holds or the timeout expires."""
    for _ in range(int(timeout / 0.1)):
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return predicate()


@contextlib.asynccontextmanager
async def _fake_dcc_peer(serve: PeerHandler) -> AsyncGenerator[asyncio.Server]:
    """Run a scripted TCP peer on the dcc-proxy upstream port."""
    server = await asyncio.start_server(serve, "127.0.0.1", DCC_PEER_PORT)
    try:
        yield server
    finally:
        server.close()
        await server.wait_closed()


async def _run_dcc_transfer(bot, serve: PeerHandler, download_path: Path) -> None:
    """Run a DCC receive through dcc-proxy until the transfer is dropped."""
    async with _fake_dcc_peer(serve):
        bot.init_dcc_connection("peer", "127.0.0.1", DCC_PROXY_PORT, "f.bin", str(download_path / "f.bin"), FILE_SIZE, 0, False, False)
        await _wait_for(lambda: not bot.current_transfers, timeout=20)


class TestIrcFaults:
    """Network faults on the IRC server connection."""

    @pytest.mark.asyncio
    async def test_connect_and_join_with_latency(self, irc_bot_factory, add_toxic: ToxicAdder):
        """A latent connection still completes registration and channel join."""
        add_toxic("irc-proxy", "lat", "latency", {"latency": 400})
        bot = irc_bot_factory(port=IRC_PROXY_PORT)
        try:
            await asyncio.wait_for(bot.connect(), timeout=30)
            assert bot.connection.connected

            # InspIRCd rejects JOIN sent before the PING/PONG handshake
            # finishes (451 notregistered), so retry until registration is done.
            joined = False
            for _ in range(20):
                await bot.join_channel(TEST_CHANNEL)
                joined = await _wait_for(lambda: TEST_CHANNEL in bot.joined_channels, timeout=2)
                if joined:
                    break
            assert joined
        finally:
            if bot.connection and bot.connection.connected:
                await bot.disconnect("test done")

    @pytest.mark.asyncio
    async def test_connect_refused_when_proxy_disabled(self, irc_bot_factory, toxiproxy):
        """A disabled proxy surfaces as a connection error from connect()."""
        status, body = toxi_request("POST", "/proxies/irc-proxy", {"enabled": False})
        assert status == 200, body
        try:
            bot = irc_bot_factory(port=IRC_PROXY_PORT)
            with pytest.raises(OSError):
                await bot.connect()
        finally:
            toxi_request("POST", "/reset")

    @pytest.mark.asyncio
    async def test_reset_peer_drops_connection(self, irc_bot_factory, add_toxic: ToxicAdder):
        """A mid-session TCP reset drops the connection without crashing the bot."""
        bot = irc_bot_factory(port=IRC_PROXY_PORT)
        try:
            await asyncio.wait_for(bot.connect(), timeout=30)
            assert bot.connection.connected

            add_toxic("irc-proxy", "rst", "reset_peer", {"timeout": 0}, stream="upstream")
            # generate traffic so the toxic fires
            bot.connection.privmsg("nobody", "ping")

            dropped = await _wait_for(lambda: not bot.connection.connected, timeout=15)
            assert dropped
        finally:
            if bot.connection and bot.connection.connected:
                await bot.disconnect("test done")


class TestDccFaults:
    """Network faults on the DCC data path via a scripted local peer."""

    @pytest.mark.asyncio
    async def test_limit_data_truncates_transfer(self, irc_bot_factory, irc_bot_manager, add_toxic: ToxicAdder):
        """The proxy cutting data mid-transfer yields a size-mismatch failure."""

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(FILE_DATA)
            with contextlib.suppress(Exception):
                await writer.drain()
                await asyncio.wait_for(reader.read(65536), timeout=10)
            writer.close()

        add_toxic("dcc-proxy", "limit", "limit_data", {"bytes": 1024})
        bot = irc_bot_factory()
        download_path = Path(irc_bot_manager.config["default_download_path"])

        await _run_dcc_transfer(bot, serve, download_path)

        transfers = irc_bot_manager.transfers.get("f.bin", [])
        assert transfers and transfers[0]["status"] == "failed"
        assert "size mismatch" in transfers[0]["error"]
        assert (download_path / "f.bin").read_bytes() == FILE_DATA[:1024]

    @pytest.mark.asyncio
    async def test_reset_peer_fails_transfer(self, irc_bot_factory, irc_bot_manager, add_toxic: ToxicAdder):
        """A TCP reset on the DCC connection terminates the transfer as failed/error."""

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(FILE_DATA)
            with contextlib.suppress(Exception):
                await writer.drain()
                await asyncio.wait_for(reader.read(65536), timeout=10)
            writer.close()

        add_toxic("dcc-proxy", "rst", "reset_peer", {"timeout": 0})
        bot = irc_bot_factory()
        download_path = Path(irc_bot_manager.config["default_download_path"])

        await _run_dcc_transfer(bot, serve, download_path)

        transfers = irc_bot_manager.transfers.get("f.bin", [])
        assert transfers and transfers[0]["status"] in ("failed", "error")

    @pytest.mark.asyncio
    async def test_sliced_delivery_completes(self, irc_bot_factory, irc_bot_manager, add_toxic: ToxicAdder):
        """Fragmented delivery still produces a complete, correct file."""

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(FILE_DATA)
            with contextlib.suppress(Exception):
                await writer.drain()
                await asyncio.wait_for(reader.read(65536), timeout=15)
            writer.close()

        add_toxic("dcc-proxy", "slicer", "slicer", {"average_size": 64, "size_variation": 32, "delay": 5000})
        bot = irc_bot_factory()
        download_path = Path(irc_bot_manager.config["default_download_path"])

        await _run_dcc_transfer(bot, serve, download_path)

        transfers = irc_bot_manager.transfers.get("f.bin", [])
        assert transfers and transfers[0]["status"] == "completed"
        assert (download_path / "f.bin").read_bytes() == FILE_DATA

    @pytest.mark.asyncio
    async def test_bandwidth_throttle_then_recover(self, irc_bot_factory, irc_bot_manager, add_toxic: ToxicAdder, toxiproxy):
        """A throttled transfer makes partial progress and completes after recovery."""

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(FILE_DATA)
            with contextlib.suppress(Exception):
                await writer.drain()
                await asyncio.wait_for(reader.read(65536), timeout=30)
            writer.close()

        add_toxic("dcc-proxy", "bw", "bandwidth", {"rate": 1})  # 1 KB/s downstream
        bot = irc_bot_factory()
        download_path = Path(irc_bot_manager.config["default_download_path"])

        async with _fake_dcc_peer(serve):
            bot.init_dcc_connection("peer", "127.0.0.1", DCC_PROXY_PORT, "f.bin", str(download_path / "f.bin"), FILE_SIZE, 0, False, False)

            # a few seconds at 1 KB/s must leave the transfer incomplete
            await asyncio.sleep(3)
            transfer = next(iter(bot.current_transfers.values()), None)
            assert transfer is not None
            assert 0 < transfer["bytes_received"] < FILE_SIZE

            # remove the toxic: remaining data flows and the transfer completes
            toxi_request("POST", "/reset")
            completed = await _wait_for(lambda: not bot.current_transfers, timeout=20)

        assert completed
        transfers = irc_bot_manager.transfers.get("f.bin", [])
        assert transfers and transfers[0]["status"] == "completed"
        assert (download_path / "f.bin").read_bytes() == FILE_DATA
