"""Fault-injection tests: injected filesystem, network, and queue failures.

Uses mocks for dependency failures plus a real localhost TCP peer for the
DCC data path, exercising real connection-reset/early-close behavior that
pure mocks cannot reach.
"""

import asyncio
import socket
import ssl
import struct
import time
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest
import irc.client

from dccbot.aiodcc import AioDCCConnection, AioReactor, DCCProtocol
from dccbot.app import IRCBotAPI
from dccbot.ircbot import IRCBot
from dccbot.manager import IRCBotManager
from dccbot.transfer_handler import TransferHandler


def _make_transfer(*, size: int = 10, offset: int = 0) -> dict:
    return {
        "nick": "sender",
        "filename": "file.bin",
        "file_path": "/tmp/file.bin",
        "start_time": time.time() - 1,
        "bytes_received": 0,
        "offset": offset,
        "size": size,
        "percent": 0,
        "last_progress_update": time.time() - 1,
        "last_progress_bytes_received": 0,
        "completed": False,
        "status": "started",
        "connected": False,
    }


def _make_bot_with_transfer(dcc: MagicMock, transfer: dict) -> MagicMock:
    bot = MagicMock()
    bot.current_transfers = {dcc: transfer}
    bot.bot_channel_map = {}
    bot.joined_channels = {}
    bot.allowed_mimetypes = None
    bot.mime_checker = MagicMock()
    bot.config = {}
    bot._add_md5_check_queue_item = MagicMock()
    return bot


@pytest.fixture
def mock_reactor(fresh_event_loop):
    """Create a mock reactor bound to the fresh test event loop."""
    reactor = MagicMock(spec=AioReactor)
    reactor.loop = fresh_event_loop
    reactor._on_connect = MagicMock()
    reactor._handle_event = MagicMock()
    reactor._remove_connection = MagicMock()
    reactor.mutex = MagicMock()
    reactor.mutex.__enter__ = MagicMock()
    reactor.mutex.__exit__ = MagicMock()
    reactor.connections = []
    return reactor


@pytest.fixture
def dcc_connection(mock_reactor):
    """Create a DCC connection for testing."""
    return AioDCCConnection(mock_reactor, "raw")


@pytest.fixture
def mock_bot_manager():
    """Create a mock bot manager with real dicts for mutable state."""
    manager = MagicMock()
    manager.config = {}
    manager.transfers = {}
    return manager


class TestAioDccFaults:
    """Injected failures in the asyncio DCC connection layer."""

    @pytest.mark.asyncio
    async def test_listen_all_ports_exhausted(self, dcc_connection, mock_reactor):
        """All ports in the range failing raises DCCConnectionError."""

        async def fail_create_server(*args, **kwargs):
            raise OSError("address in use")

        mock_reactor.loop.create_server = fail_create_server

        with pytest.raises(irc.client.DCCConnectionError):
            await dcc_connection.listen(port=(15000, 15002))

    @pytest.mark.asyncio
    async def test_listen_hostname_resolution_failure_falls_back(self, dcc_connection, mock_reactor):
        """gethostbyname failure falls back to 127.0.0.1 for the advertised address."""

        async def mock_create_server(factory, *args, **kwargs):
            mock_server = MagicMock()
            mock_socket = MagicMock()
            mock_socket.getsockname.return_value = ("0.0.0.0", 54321)
            mock_server.sockets = [mock_socket]
            return mock_server

        mock_reactor.loop.create_server = mock_create_server

        with patch("dccbot.aiodcc.socket.gethostbyname", side_effect=OSError("no dns")):
            await dcc_connection.listen()

        assert dcc_connection.localaddress == "127.0.0.1"
        assert dcc_connection.localport == 54321

    def test_connection_made_missing_peername(self, fresh_event_loop):
        """A transport without peername leaves peer address/port as None."""
        connection = MagicMock()
        connection.passive = True
        connection.connected = False
        connection.server = None
        protocol = DCCProtocol(connection, fresh_event_loop)

        transport = MagicMock()
        transport.get_extra_info.return_value = None
        protocol.connection_made(transport)

        assert connection.peeraddress is None
        assert connection.peerport is None
        connection.reactor._handle_event.assert_called_once()

    def test_send_bytes_missing_transport_disconnects(self, dcc_connection, mock_reactor):
        """A missing transport triggers disconnect instead of a raw crash."""
        dcc_connection.connected = True
        dcc_connection.peeraddress = "127.0.0.1"
        # transport is never set, so accessing it raises AttributeError

        dcc_connection.send_bytes(b"data")

        mock_reactor._handle_event.assert_called_once()
        mock_reactor._remove_connection.assert_called_once()

    def test_disconnect_unregisters_when_handler_raises(self, dcc_connection, mock_reactor):
        """A failing dcc_disconnect handler must not leak the connection."""
        dcc_connection.peeraddress = "127.0.0.1"
        mock_reactor._handle_event.side_effect = RuntimeError("handler boom")

        with pytest.raises(RuntimeError):
            dcc_connection.disconnect("bye")

        mock_reactor._remove_connection.assert_called_once_with(dcc_connection)

    def test_process_data_handler_error_disconnects(self, dcc_connection, mock_reactor):
        """A crashing dccmsg handler disconnects instead of leaving a zombie."""
        dcc_connection.dcctype = "raw"
        dcc_connection.peeraddress = "127.0.0.1"
        mock_reactor._handle_event.side_effect = RuntimeError("handler boom")

        with pytest.raises(RuntimeError):
            dcc_connection.process_data(b"data")

        mock_reactor._remove_connection.assert_called_once_with(dcc_connection)

    @pytest.mark.asyncio
    async def test_connect_factory_raises_marks_transfer_error(self, dcc_connection, mock_reactor):
        """Connection factory failures mark the transfer item as error."""

        async def failing_factory(*args):
            raise OSError("connection refused")

        connect_factory = MagicMock(return_value=failing_factory())
        transfer_item = {"status": "started"}

        await dcc_connection.connect("127.0.0.1", 5000, connect_factory, transfer_item)

        assert dcc_connection.connected is False
        assert transfer_item["status"] == "error"
        assert "connection refused" in transfer_item["error"]
        # the failed connection must be unregistered so it cannot block idle cleanup
        mock_reactor._remove_connection.assert_called_once_with(dcc_connection)


class TestTransferHandlerFaults:
    """Injected failures in the DCC data path."""

    def test_mime_checker_exception_aborts_transfer(self):
        """A failing MIME check aborts the transfer instead of crashing the handler."""
        dcc = MagicMock()
        transfer = _make_transfer(size=1024)
        bot = _make_bot_with_transfer(dcc, transfer)
        bot.allowed_mimetypes = ["video/mp4"]
        bot.mime_checker.from_buffer.side_effect = RuntimeError("magic failed")
        handler = TransferHandler(bot)
        event = MagicMock()
        event.arguments = [b"abc"]

        handler.on_dccmsg(dcc, event)

        assert transfer["status"] == "error"
        assert "MIME" in transfer["error"]
        dcc.disconnect.assert_called_once()

    def test_abort_transfer_tolerates_disconnect_failure(self):
        """A failing disconnect() still marks and drops the transfer."""
        dcc = MagicMock()
        dcc.disconnect.side_effect = RuntimeError("disconnect boom")
        transfer = _make_transfer(size=1024)
        bot = _make_bot_with_transfer(dcc, transfer)
        handler = TransferHandler(bot)

        handler._abort_transfer(dcc, transfer, "write failed")

        assert transfer["status"] == "error"
        assert transfer["error"] == "write failed"
        assert dcc not in bot.current_transfers

    def test_finalize_getsize_failure_marks_error(self, tmp_path):
        """getsize raising on an existing file marks the transfer as error."""
        dcc = MagicMock()
        file_path = tmp_path / "file.bin"
        file_path.write_bytes(b"x" * 4)
        transfer = _make_transfer(size=4)
        transfer["bytes_received"] = 4
        transfer["file_path"] = str(file_path)
        bot = _make_bot_with_transfer(dcc, transfer)
        handler = TransferHandler(bot)
        event = MagicMock()
        event.arguments = []

        with patch("os.path.getsize", side_effect=OSError("i/o error")):
            handler.on_dcc_disconnect(dcc, event)

        assert transfer["status"] == "error"
        assert dcc not in bot.current_transfers

    def test_send_ack_4gib_boundary(self):
        """ACK width switches from 32- to 64-bit exactly at 4 GiB."""
        for size, fmt in ((4 * 1024**3 - 1, "!I"), (4 * 1024**3, "!Q")):
            dcc = MagicMock()
            transfer = _make_transfer(size=size)
            bot = _make_bot_with_transfer(dcc, transfer)
            handler = TransferHandler(bot)
            transfer["bytes_received"] = 3

            handler._send_ack(dcc, transfer)
            dcc.send_bytes.assert_called_once_with(struct.pack(fmt, 3))

    def test_on_dccmsg_zero_size_does_not_crash(self):
        """A zero-size transfer record must not crash progress reporting."""
        dcc = MagicMock()
        transfer = _make_transfer(size=0)
        bot = _make_bot_with_transfer(dcc, transfer)
        handler = TransferHandler(bot)
        event = MagicMock()
        event.arguments = [b"abc"]

        with patch("builtins.open", mock_open()):
            try:
                handler.on_dccmsg(dcc, event)
            except ZeroDivisionError:
                pytest.fail("zero-size transfer crashed progress reporting")

    def test_over_send_aborts_transfer(self):
        """A peer sending more than the declared size is aborted, not written."""
        dcc = MagicMock()
        transfer = _make_transfer(size=10)
        bot = _make_bot_with_transfer(dcc, transfer)
        handler = TransferHandler(bot)
        event = MagicMock()
        event.arguments = [b"x" * 11]

        with patch("builtins.open", mock_open()) as m:
            handler.on_dccmsg(dcc, event)

        m.assert_not_called()
        assert transfer["status"] == "failed"
        assert "more data than declared" in transfer["error"]
        dcc.disconnect.assert_called_once()
        dcc.send_bytes.assert_not_called()
        assert dcc not in bot.current_transfers

    def test_over_send_counts_resume_offset(self):
        """The declared-size check accounts for resume offset + received bytes."""
        dcc = MagicMock()
        transfer = _make_transfer(size=10, offset=8)
        transfer["bytes_received"] = 1  # 9 of 10 received; only 1 byte remains
        bot = _make_bot_with_transfer(dcc, transfer)
        handler = TransferHandler(bot)
        event = MagicMock()
        event.arguments = [b"x" * 2]

        with patch("builtins.open", mock_open()) as m:
            handler.on_dccmsg(dcc, event)

        m.assert_not_called()
        assert transfer["status"] == "failed"
        dcc.disconnect.assert_called_once()


class TestManagerFaults:
    """Injected failures in the bot manager."""

    def test_get_md5_missing_file(self):
        """MD5 of a missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            IRCBotManager.get_md5("/nonexistent/file.bin")

    @pytest.mark.asyncio
    async def test_check_queue_processor_survives_md5_failure(self, tmp_path):
        """A failing MD5 job is dropped and the processor keeps working."""
        real_file = tmp_path / "real.bin"
        real_file.write_bytes(b"data")

        manager = MagicMock()
        manager.transfers = {"real.bin": [{"id": "job2"}]}

        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(IRCBotManager.check_queue_processor(manager, asyncio.get_running_loop(), queue))
        try:
            await queue.put({"filename": "gone.bin", "file_path": str(tmp_path / "gone.bin"), "id": "job1"})
            await queue.put({"filename": "real.bin", "file_path": str(real_file), "id": "job2"})
            await asyncio.wait_for(queue.join(), timeout=10)
        finally:
            task.cancel()

        assert manager.transfers["real.bin"][0]["file_md5"] == IRCBotManager.get_md5(str(real_file))

    @pytest.mark.asyncio
    async def test_cleanup_loop_survives_handler_exception(self):
        """An exception in the cleanup body is logged and the loop retries."""
        manager = MagicMock()
        manager._cleanup_bots = AsyncMock(side_effect=[RuntimeError("boom"), None, None])
        manager._cleanup_transfers = AsyncMock()

        sleep_calls = 0

        async def fake_sleep(_seconds):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls >= 3:
                raise asyncio.CancelledError

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await IRCBotManager.cleanup(manager)

        assert manager._cleanup_bots.call_count >= 2

    @pytest.mark.asyncio
    async def test_cleanup_bots_survives_per_bot_failure(self, config_file_factory):
        """A failing per-bot cleanup does not skip other bots or idle disconnects."""
        manager = IRCBotManager(config_file_factory({"servers": {}}))
        manager.server_idle_timeout = 1

        bad_bot = MagicMock()
        bad_bot.joined_channels = {"#a": time.time()}
        bad_bot.current_transfers = {"x": {}}
        bad_bot.command_queue.empty.return_value = False
        bad_bot.last_active = time.time()
        bad_bot.cleanup = AsyncMock(side_effect=RuntimeError("boom"))

        idle_bot = MagicMock()
        idle_bot.joined_channels = {}
        idle_bot.current_transfers = {}
        idle_bot.command_queue.empty.return_value = True
        idle_bot.last_active = time.time() - 100
        idle_bot.disconnect = AsyncMock(side_effect=RuntimeError("gone already"))

        manager.bots = {"bad": bad_bot, "idle": idle_bot}

        await manager._cleanup_bots()

        bad_bot.cleanup.assert_called_once()
        # idle bot is removed even though its disconnect raised
        assert "idle" not in manager.bots
        idle_bot.disconnect.assert_awaited_once()


class TestIrcbotFaults:
    """Injected failures in IRCBot setup/teardown paths."""

    @pytest.mark.asyncio
    async def test_passive_dcc_listen_failure_no_transfer(self, mock_bot_manager, tmp_path):
        """A failing passive listener logs the error and registers nothing."""
        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, str(tmp_path), None, 1000, mock_bot_manager)
        bot.connection = MagicMock()
        mock_dcc = MagicMock()
        mock_dcc.listen = AsyncMock(side_effect=irc.client.DCCConnectionError("bind failed"))

        with patch.object(bot, "dcc", return_value=mock_dcc), patch.object(bot.loop, "create_task") as mock_create_task:
            bot.init_passive_dcc_connection("sender", "f.bin", 1000)
            coro = mock_create_task.call_args[0][0]
            await coro

        assert bot.current_transfers == {}
        bot.connection.ctcp_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_passive_dcc_ctcp_failure_disconnects_listener(self, mock_bot_manager, tmp_path):
        """A failed CTCP advertisement tears down the bound listener."""
        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, str(tmp_path), None, 1000, mock_bot_manager)
        bot.connection = MagicMock()
        bot.connection.ctcp_reply.side_effect = RuntimeError("link dead")
        mock_dcc = MagicMock()
        mock_dcc.listen = AsyncMock()
        mock_dcc.localaddress = "127.0.0.1"
        mock_dcc.localport = 5000

        with patch.object(bot, "dcc", return_value=mock_dcc), patch.object(bot.loop, "create_task") as mock_create_task:
            bot.init_passive_dcc_connection("sender", "f.bin", 1000)
            coro = mock_create_task.call_args[0][0]
            await coro

        mock_dcc.disconnect.assert_called_once()
        assert bot.current_transfers == {}

    @pytest.mark.asyncio
    async def test_wait_passive_timeout_marks_failed_and_tolerates_disconnect_error(self, mock_bot_manager):
        """An idle passive listener is failed; disconnect errors are swallowed."""
        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, "/tmp/dl", None, 1000, mock_bot_manager)
        dcc = MagicMock()
        dcc.connected = False
        dcc.disconnect.side_effect = RuntimeError("already gone")
        transfer = {"connected": False, "status": "started"}
        bot.current_transfers = {dcc: transfer}

        await bot._wait_passive_timeout(dcc, transfer, 0)

        assert transfer["status"] == "failed"
        assert "Passive DCC timeout" in transfer["error"]
        assert dcc not in bot.current_transfers

    def test_init_dcc_connection_makedirs_failure(self, mock_bot_manager, loop_patch):
        """A download dir that cannot be created aborts the transfer setup."""
        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, "/tmp/dl", None, 1000, mock_bot_manager)
        bot.connection = MagicMock()

        mock_loop = MagicMock()
        with patch("os.makedirs", side_effect=PermissionError("denied")), patch.object(bot, "loop", mock_loop):
            bot.init_dcc_connection("sender", "1.2.3.4", 5000, "f.bin", "/tmp/dl/f.bin", 100, 0, False, False)

        assert bot.current_transfers == {}
        mock_loop.create_task.assert_not_called()

    def test_init_dcc_connection_ssl_context_failure_drops_dcc(self, mock_bot_manager, loop_patch):
        """An SSL context failure after dcc() removes the registered connection."""
        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, "/tmp/dl", None, 1000, mock_bot_manager)
        bot.connection = MagicMock()
        mock_dcc = MagicMock()

        mock_loop = MagicMock()
        with (
            patch.object(bot, "dcc", return_value=mock_dcc),
            patch.object(bot, "_get_dcc_ssl_context", side_effect=RuntimeError("cert boom")),
            patch.object(bot, "loop", mock_loop),
        ):
            bot.init_dcc_connection("sender", "1.2.3.4", 5000, "f.bin", "/tmp/dl/f.bin", 100, 0, True, False)

        mock_dcc.disconnect.assert_called_once()
        mock_loop.create_task.assert_not_called()
        assert bot.current_transfers == {}

    @pytest.mark.asyncio
    async def test_connect_failure_propagates(self, mock_bot_manager):
        """IRC connect errors propagate after logging."""
        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, "/tmp/dl", None, 1000, mock_bot_manager)
        mock_conn = MagicMock()
        mock_conn.connect = AsyncMock(side_effect=OSError("no route to host"))

        with patch("dccbot.ircbot.AioConnection", return_value=mock_conn):
            with pytest.raises(OSError):
                await bot.connect()

    @pytest.mark.asyncio
    async def test_process_command_queue_survives_handler_exception(self, mock_bot_manager):
        """A failing command handler is logged and the queue keeps draining."""
        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, "/tmp/dl", None, 1000, mock_bot_manager)
        bot.connection = MagicMock()

        mock_handler = AsyncMock(side_effect=[RuntimeError("boom"), None])
        with patch.object(bot, "_handle_send_command", mock_handler):
            task = asyncio.create_task(bot.process_command_queue())
            try:
                await bot.command_queue.put({"command": "send", "user": "u", "message": "one"})
                await bot.command_queue.put({"command": "send", "user": "u", "message": "two"})
                for _ in range(50):
                    if mock_handler.call_count >= 2:
                        break
                    await asyncio.sleep(0.02)
            finally:
                task.cancel()

        assert mock_handler.call_count == 2

    @pytest.mark.asyncio
    async def test_process_command_queue_skips_unknown_command(self, mock_bot_manager):
        """Commands without a registered handler are skipped."""
        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, "/tmp/dl", None, 1000, mock_bot_manager)
        bot.connection = MagicMock()

        task = asyncio.create_task(bot.process_command_queue())
        try:
            await bot.command_queue.put({"command": "nonexistent"})
            await bot.command_queue.put({})
            await asyncio.sleep(0.2)
        finally:
            task.cancel()


class TestAppFaults:
    """Injected failures in the aiohttp API layer."""

    @pytest.mark.asyncio
    async def test_broadcast_json_drops_reset_connections(self):
        """A reset websocket is discarded; healthy clients still get the payload."""
        api = IRCBotAPI(config_file="config.json", bot_manager=MagicMock())
        bad_ws = MagicMock()
        bad_ws.closed = False
        bad_ws.send_str = AsyncMock(side_effect=ConnectionResetError("gone"))
        good_ws = MagicMock()
        good_ws.closed = False
        good_ws.send_str = AsyncMock()
        api.websockets = {bad_ws, good_ws}

        await api._broadcast_json({"type": "transfers", "transfers": []})

        assert bad_ws not in api.websockets
        good_ws.send_str.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_binary_message_ignored(self, ws_session):
        """Binary frames are ignored and the connection stays usable."""
        ws, _ = ws_session
        await ws.send_bytes(b"\x00\x01\x02")
        await ws.send_str("/help")
        msg = await ws.receive_json()
        assert msg["status"] == "ok"

    @pytest.mark.asyncio
    async def test_broadcast_loop_survives_snapshot_error(self):
        """A snapshot failure is logged and the broadcast loop keeps running."""
        api = IRCBotAPI(config_file="config.json", bot_manager=MagicMock())
        api._broadcast_transfers_to_clients = AsyncMock(side_effect=[RuntimeError("boom"), None, None])  # type: ignore

        sleeps = 0

        async def fake_sleep(_seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps >= 2:
                raise asyncio.CancelledError

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await api.broadcast_transfers()

        assert api._broadcast_transfers_to_clients.call_count == 2

    def test_ws_log_handler_emit_without_loop_leaks_nothing(self):
        """emit() with no event loop sends nothing and creates no coroutine."""
        import gc
        import logging
        import warnings

        from dccbot.app import WebSocketLogHandler

        handler = WebSocketLogHandler(set())
        ws = MagicMock()
        ws.closed = False
        calls = []

        async def send_str(payload):
            calls.append(payload)

        ws.send_str = send_str
        handler.websockets.add(ws)

        record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            handler.emit(record)
            gc.collect()

        assert calls == []
        assert not [w for w in caught if "never awaited" in str(w.message)]


class TestSslUtilFaults:
    """Injected failures in certificate handling."""

    def test_create_dcc_ssl_context_missing_files(self, tmp_path):
        """Missing cert/key files surface as a load failure."""
        with pytest.raises((ssl.SSLError, OSError)):
            from dccbot.ssl_util import create_dcc_ssl_context

            create_dcc_ssl_context(False, str(tmp_path / "no-cert.pem"), str(tmp_path / "no-key.pem"))

    def test_cert_generation_unwritable_dir(self, tmp_path):
        """An unwritable cache dir surfaces the permission error."""
        from dccbot.ssl_util import _generate_self_signed_cert

        with patch("pathlib.Path.write_bytes", side_effect=PermissionError("denied")):
            with pytest.raises(PermissionError):
                _generate_self_signed_cert(tmp_path / "c.pem", tmp_path / "k.pem")


class TestRealSocketFaults:
    """Faults on a real localhost TCP connection to the DCC data path."""

    async def _run_transfer(self, mock_bot_manager, tmp_path, serve):
        """Start a scripted TCP peer and run a real DCC receive against it."""
        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, str(tmp_path), None, 1000, mock_bot_manager)
        bot.connection = MagicMock()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        bot.init_dcc_connection("peer", "127.0.0.1", port, "f.bin", str(tmp_path / "f.bin"), 100, 0, False, False)

        try:
            for _ in range(100):
                if not bot.current_transfers:
                    break
                await asyncio.sleep(0.05)
        finally:
            server.close()
            await server.wait_closed()
        return bot

    @pytest.mark.asyncio
    async def test_peer_closes_immediately_marks_failed(self, mock_bot_manager, tmp_path):
        """A peer that accepts then closes yields a failed transfer."""

        async def serve(reader, writer):
            writer.close()

        bot = await self._run_transfer(mock_bot_manager, tmp_path, serve)

        assert bot.current_transfers == {}
        transfers = mock_bot_manager.transfers.get("f.bin", [])
        # either the connect itself failed or EOF hit before any data was written
        assert transfers and transfers[0]["status"] == "error"
        assert transfers[0]["error"]

    @pytest.mark.asyncio
    async def test_peer_sends_partial_then_closes(self, mock_bot_manager, tmp_path):
        """A truncated send produces a size-mismatch failure and a partial file."""

        async def serve(reader, writer):
            writer.write(b"x" * 40)
            await writer.drain()
            try:
                await asyncio.wait_for(reader.read(1024), timeout=5)
            except (asyncio.TimeoutError, ConnectionError):
                pass
            writer.close()

        bot = await self._run_transfer(mock_bot_manager, tmp_path, serve)

        transfers = mock_bot_manager.transfers.get("f.bin", [])
        assert transfers and transfers[0]["status"] == "failed"
        assert "size mismatch" in transfers[0]["error"]
        assert (tmp_path / "f.bin").stat().st_size == 40

    @pytest.mark.asyncio
    async def test_peer_sends_nothing_stays_in_progress(self, mock_bot_manager, tmp_path):
        """A silent peer leaves the transfer connected but unfinished."""

        hold_open = asyncio.Event()

        async def serve(reader, writer):
            await asyncio.wait_for(hold_open.wait(), timeout=5)
            writer.close()

        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, str(tmp_path), None, 1000, mock_bot_manager)
        bot.connection = MagicMock()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        bot.init_dcc_connection("peer", "127.0.0.1", port, "f.bin", str(tmp_path / "f.bin"), 100, 0, False, False)

        try:
            # wait until the TCP connection is established (not for data, which never comes)
            for _ in range(50):
                if bot.current_transfers and all(dcc.connected for dcc in bot.current_transfers):
                    break
                await asyncio.sleep(0.05)

            transfer = next(iter(bot.current_transfers.values()), None)
            assert transfer is not None
            assert transfer["status"] in ("started", "in_progress")
            assert transfer["bytes_received"] == 0
        finally:
            hold_open.set()
            server.close()
            await server.wait_closed()
            for _ in range(50):
                if not bot.current_transfers:
                    break
                await asyncio.sleep(0.05)
