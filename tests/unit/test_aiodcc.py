"""Tests for aiodcc module (DCC connection handling)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dccbot.aiodcc import AioDCCConnection, AioReactor, DCCProtocol, NonStrictDecodingLineBuffer


def test_nonstrict_decoding_line_buffer():
    """Test NonStrictDecodingLineBuffer error handling."""
    buffer = NonStrictDecodingLineBuffer()
    assert buffer.errors == "replace"


def test_dcc_protocol():
    """Test DCCProtocol instantiation."""
    reactor = MagicMock()
    loop = MagicMock()
    connection = MagicMock()
    protocol = DCCProtocol(connection, loop)
    assert protocol is not None


@pytest.fixture
def mock_reactor(fresh_event_loop):
    """Create a mock reactor."""
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
    conn = AioDCCConnection(mock_reactor, "raw")
    return conn


@pytest.mark.asyncio
async def test_dcc_connection_connect_success(dcc_connection, mock_reactor):
    """Test successful DCC connection."""
    mock_transport = MagicMock()
    mock_protocol = MagicMock()

    async def mock_factory(*args):
        return (mock_transport, mock_protocol)

    mock_connect_factory = MagicMock()
    mock_connect_factory.return_value = mock_factory()

    result = await dcc_connection.connect("127.0.0.1", 5000, mock_connect_factory)

    assert result == dcc_connection
    assert dcc_connection.connected is True
    assert dcc_connection.peeraddress == "127.0.0.1"
    assert dcc_connection.peerport == 5000


@pytest.mark.asyncio
async def test_dcc_connection_connect_failure(dcc_connection, mock_reactor):
    """Test DCC connection failure."""
    async def mock_factory(*args):
        raise ConnectionError("Connection failed")

    mock_connect_factory = MagicMock()
    mock_connect_factory.return_value = mock_factory()

    transfer_item = {"status": "pending"}
    result = await dcc_connection.connect("127.0.0.1", 5000, mock_connect_factory, transfer_item)

    assert result == dcc_connection
    assert dcc_connection.connected is False
    assert transfer_item.get("status") == "error"
    assert "Connection failed" in transfer_item.get("error", "")


def test_dcc_connection_disconnect(dcc_connection, mock_reactor):
    """Test DCC disconnection."""
    dcc_connection.connected = True
    dcc_connection.transport = MagicMock()
    dcc_connection.peeraddress = "127.0.0.1"

    dcc_connection.disconnect("Test disconnect")

    dcc_connection.transport.close.assert_called_once()
    mock_reactor._handle_event.assert_called_once()
    mock_reactor._remove_connection.assert_called_once()


def test_dcc_connection_disconnect_not_connected(dcc_connection):
    """Test disconnecting when not connected."""
    # Should not raise an error
    dcc_connection.disconnect()


def test_dcc_connection_process_data_raw(dcc_connection, mock_reactor):
    """Test processing raw data."""
    dcc_connection.dcctype = "raw"
    dcc_connection.peeraddress = "127.0.0.1"
    dcc_connection.passive = False
    dcc_connection.connected = True

    test_data = b"test data"
    dcc_connection.process_data(test_data)

    mock_reactor._handle_event.assert_called_once()
    event = mock_reactor._handle_event.call_args[0][1]
    assert event.arguments[0] == test_data


def test_dcc_connection_process_data_chat(dcc_connection, mock_reactor):
    """Test processing chat data."""
    dcc_connection.dcctype = "chat"
    dcc_connection.peeraddress = "127.0.0.1"
    dcc_connection.passive = False
    dcc_connection.connected = True
    dcc_connection.buffer = NonStrictDecodingLineBuffer()

    test_data = b"test message\n"
    dcc_connection.process_data(test_data)

    mock_reactor._handle_event.assert_called()


def test_dcc_connection_process_data_chat_too_large(dcc_connection, mock_reactor):
    """Test processing chat data that's too large."""
    dcc_connection.dcctype = "chat"
    dcc_connection.peeraddress = "127.0.0.1"
    dcc_connection.passive = False
    dcc_connection.connected = True
    dcc_connection.buffer = NonStrictDecodingLineBuffer()
    dcc_connection.disconnect = MagicMock()

    # Send data larger than 16k without newline
    test_data = b"x" * 20000
    dcc_connection.process_data(test_data)

    dcc_connection.disconnect.assert_called_once()


def test_dcc_connection_send_bytes(dcc_connection):
    """Test sending bytes."""
    dcc_connection.transport = MagicMock()
    test_data = b"test data"

    dcc_connection.send_bytes(test_data)

    dcc_connection.transport.write.assert_called_once_with(test_data)


def test_dcc_connection_send_bytes_oserror(dcc_connection):
    """Test sending bytes with OSError."""
    dcc_connection.transport = MagicMock()
    dcc_connection.transport.write.side_effect = OSError("Connection reset")
    dcc_connection.disconnect = MagicMock()

    test_data = b"test data"
    dcc_connection.send_bytes(test_data)

    dcc_connection.disconnect.assert_called_once_with("Connection reset by peer.")


def test_dcc_connection_send_bytes_not_connected(dcc_connection):
    """Test sending bytes when not connected does not raise."""
    # transport is None by default - should not raise
    dcc_connection.disconnect = MagicMock()
    dcc_connection.send_bytes(b"hello")
    dcc_connection.disconnect.assert_called_once_with("Connection reset by peer.")


def test_dcc_connection_privmsg_chat(dcc_connection):
    """Test privmsg adds newline for chat type."""
    dcc_connection.dcctype = "chat"
    dcc_connection.transport = MagicMock()
    dcc_connection.privmsg("hello")
    dcc_connection.transport.write.assert_called_once_with(b"hello\n")


def test_dcc_connection_privmsg_raw(dcc_connection):
    """Test privmsg does not add newline for raw type."""
    dcc_connection.dcctype = "raw"
    dcc_connection.transport = MagicMock()
    dcc_connection.privmsg("hello")
    dcc_connection.transport.write.assert_called_once_with(b"hello")


@pytest.mark.asyncio
async def test_dcc_connection_listen(dcc_connection, mock_reactor):
    """Test listen() creates a server."""
    import socket

    async def mock_create_server(factory, *args, **kwargs):
        mock_server = MagicMock()
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
        mock_server.sockets = [mock_socket]
        return mock_server

    mock_reactor.loop.create_server = mock_create_server

    result = await dcc_connection.listen()

    assert result == dcc_connection
    assert dcc_connection.passive is True
    assert dcc_connection.localaddress == "127.0.0.1"
    assert dcc_connection.localport == 54321


@pytest.mark.asyncio
async def test_dcc_connection_listen_with_ssl(dcc_connection, mock_reactor):
    """Test listen() passes an SSL context to create_server."""
    import ssl as ssl_module

    captured = {}

    async def mock_create_server(factory, *args, **kwargs):
        captured["ssl"] = kwargs.get("ssl")
        mock_server = MagicMock()
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
        mock_server.sockets = [mock_socket]
        return mock_server

    mock_reactor.loop.create_server = mock_create_server
    ssl_ctx = ssl_module.SSLContext(ssl_module.PROTOCOL_TLS_SERVER)

    result = await dcc_connection.listen(ssl=ssl_ctx)

    assert result == dcc_connection
    assert dcc_connection.passive is True
    assert captured["ssl"] is ssl_ctx


@pytest.mark.asyncio
async def test_dcc_connection_listen_port_range(dcc_connection, mock_reactor):
    """Test listen() picks a port inside the requested range, retrying on failure."""
    call_log: list[tuple] = []

    async def mock_create_server(factory, host, port, *args, **kwargs):
        call_log.append((host, port))
        if port < 15005:
            raise OSError(f"port {port} in use")
        mock_server = MagicMock()
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = (host, port)
        mock_server.sockets = [mock_socket]
        return mock_server

    mock_reactor.loop.create_server = mock_create_server

    result = await dcc_connection.listen(port=(15000, 15010))

    assert result == dcc_connection
    assert dcc_connection.passive is True
    assert 15000 <= dcc_connection.localport <= 15010
    assert all(15000 <= p <= 15010 for _, p in call_log)


@pytest.mark.asyncio
async def test_dcc_connection_listen_and_accept(dcc_connection, mock_reactor):
    """Test listen() followed by passive connection via connection_made."""
    async def mock_create_server(factory, *args, **kwargs):
        mock_server = MagicMock()
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
        mock_server.sockets = [mock_socket]
        return mock_server

    mock_reactor.loop.create_server = mock_create_server
    await dcc_connection.listen()

    mock_transport = MagicMock()
    mock_transport.get_extra_info.return_value = ("192.168.1.1", 12345)

    protocol = dcc_connection.protocol_class(dcc_connection, mock_reactor.loop)
    protocol.connection_made(mock_transport)

    assert dcc_connection.connected is True
    assert dcc_connection.transport is mock_transport
    assert dcc_connection.peeraddress == "192.168.1.1"
    assert dcc_connection.peerport == 12345
    dcc_connection.server.close.assert_called_once()
    mock_transport.get_extra_info.assert_called_with("peername")

    conn, event = mock_reactor._handle_event.call_args[0]
    assert conn is dcc_connection
    assert event.type == "dcc_connect"
    assert event.source == "192.168.1.1"


@pytest.mark.asyncio
async def test_dcc_connection_disconnect_with_server(dcc_connection, mock_reactor):
    """Test disconnect closes server and transport."""
    async def mock_create_server(factory, *args, **kwargs):
        mock_server = MagicMock()
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
        mock_server.sockets = [mock_socket]
        return mock_server

    mock_reactor.loop.create_server = mock_create_server
    await dcc_connection.listen()

    mock_transport = MagicMock()
    dcc_connection.transport = mock_transport
    dcc_connection.connected = True
    dcc_connection.peeraddress = "127.0.0.1"

    dcc_connection.disconnect("test message")

    dcc_connection.server.close.assert_called_once()
    mock_transport.close.assert_called_once()
    mock_reactor._handle_event.assert_called_once()
    mock_reactor._remove_connection.assert_called_once()

    # Calling disconnect again should be a no-op (idempotent)
    dcc_connection.disconnect("test message again")
    mock_transport.close.assert_called_once()


@pytest.mark.asyncio
async def test_aio_reactor_dcc():
    """Test AioReactor.dcc() method."""
    loop = asyncio.get_running_loop()
    reactor = AioReactor()
    reactor.loop = loop
    reactor.mutex = MagicMock()
    reactor.mutex.__enter__ = MagicMock()
    reactor.mutex.__exit__ = MagicMock()
    reactor.connections = []

    dcc = reactor.dcc("raw")

    assert isinstance(dcc, AioDCCConnection)
    assert dcc.dcctype == "raw"
    assert dcc in reactor.connections


@pytest.mark.asyncio
async def test_dcc_connection_connect_initializes_state(dcc_connection, mock_reactor):
    """connect() should reset per-connection state and register the transport."""
    mock_transport = MagicMock()
    mock_protocol = MagicMock()

    async def mock_factory(*args):
        return (mock_transport, mock_protocol)

    mock_connect_factory = MagicMock()
    mock_connect_factory.return_value = mock_factory()

    await dcc_connection.connect("127.0.0.1", 5000, mock_connect_factory)

    assert dcc_connection.handlers == {}
    assert dcc_connection.buffer is not None
    assert dcc_connection._disconnected is False
    assert dcc_connection.transport is mock_transport
    assert dcc_connection.protocol is mock_protocol
    mock_reactor._on_connect.assert_called_once_with(mock_protocol, mock_transport)


def test_dcc_connection_disconnect_event_fields(dcc_connection, mock_reactor):
    """disconnect() should emit a well-formed dcc_disconnect event."""
    dcc_connection.connected = True
    dcc_connection.transport = MagicMock()
    dcc_connection.peeraddress = "10.0.0.1"

    dcc_connection.disconnect("bye")

    event = mock_reactor._handle_event.call_args[0][1]
    assert event.type == "dcc_disconnect"
    assert event.source == "10.0.0.1"
    assert event.target == ""
    assert event.arguments == ["bye"]


def test_dcc_connection_disconnect_default_message(dcc_connection, mock_reactor):
    """disconnect() without a message emits an empty-string argument."""
    dcc_connection.connected = True
    dcc_connection.transport = MagicMock()
    dcc_connection.peeraddress = "10.0.0.1"

    dcc_connection.disconnect()

    event = mock_reactor._handle_event.call_args[0][1]
    assert event.arguments == [""]


def test_process_data_event_source_is_peer(dcc_connection, mock_reactor):
    """Raw-mode events should carry the peer address as source."""
    dcc_connection.dcctype = "raw"
    dcc_connection.peeraddress = "10.0.0.2"
    dcc_connection.passive = False
    dcc_connection.connected = True

    dcc_connection.process_data(b"chunk")

    event = mock_reactor._handle_event.call_args[0][1]
    assert event.type == "dccmsg"
    assert event.source == "10.0.0.2"
    assert event.target is None
    assert event.arguments == [b"chunk"]


def test_process_data_buffer_at_limit_survives(dcc_connection, mock_reactor):
    """A chat buffer at exactly 16k without newline must not disconnect."""
    dcc_connection.dcctype = "chat"
    dcc_connection.peeraddress = "127.0.0.1"
    dcc_connection.passive = False
    dcc_connection.connected = True
    dcc_connection.buffer = NonStrictDecodingLineBuffer()
    dcc_connection.disconnect = MagicMock()

    dcc_connection.process_data(b"x" * (2**14))

    dcc_connection.disconnect.assert_not_called()


def test_process_data_buffer_just_over_limit_disconnects(dcc_connection, mock_reactor):
    """A chat buffer just over 16k without newline disconnects."""
    dcc_connection.dcctype = "chat"
    dcc_connection.peeraddress = "127.0.0.1"
    dcc_connection.passive = False
    dcc_connection.connected = True
    dcc_connection.buffer = NonStrictDecodingLineBuffer()
    dcc_connection.disconnect = MagicMock()

    dcc_connection.process_data(b"x" * (2**14 + 1))

    dcc_connection.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_dcc_connection_listen_initializes_state(dcc_connection, mock_reactor):
    """listen() should reset per-connection state and pass the socket family."""
    import socket

    captured = {}

    async def mock_create_server(factory, host, port, *args, **kwargs):
        captured["factory"] = factory
        captured["kwargs"] = kwargs
        mock_server = MagicMock()
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
        mock_server.sockets = [mock_socket]
        return mock_server

    mock_reactor.loop.create_server = mock_create_server
    dcc_connection.handlers = {"stale": True}

    await dcc_connection.listen()

    assert dcc_connection.passive is True
    assert dcc_connection.connected is False
    assert dcc_connection._disconnected is False
    assert dcc_connection.handlers == {}
    assert dcc_connection.buffer is not None
    assert captured["kwargs"]["family"] == socket.AF_INET

    # The protocol factory must bind the protocol back to this connection.
    protocol = captured["factory"]()
    assert protocol.connection is dcc_connection
    assert protocol.loop is mock_reactor.loop


@pytest.mark.asyncio
async def test_dcc_connection_listen_host_args(dcc_connection, mock_reactor):
    """listen() resolves the bind host and passes it to create_server."""
    captured = {}

    async def mock_create_server(factory, host, port, *args, **kwargs):
        captured["host"] = host
        captured["port"] = port
        mock_server = MagicMock()
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
        mock_server.sockets = [mock_socket]
        return mock_server

    mock_reactor.loop.create_server = mock_create_server

    await dcc_connection.listen(addr="192.0.2.1", port=15000)
    assert captured["host"] == "192.0.2.1"
    assert captured["port"] == 15000

    await dcc_connection.listen(addr=("10.0.0.9", 16000))
    assert captured["host"] == "10.0.0.9"
    assert captured["port"] == 16000

    # A bare host string without a port must default to port 0 (OS-assigned).
    await dcc_connection.listen(addr="192.0.2.1")
    assert captured["host"] == "192.0.2.1"
    assert captured["port"] == 0


@pytest.mark.asyncio
async def test_dcc_connection_listen_default_port_zero(dcc_connection, mock_reactor):
    """listen() without addr/port asks the OS for a random port (port 0)."""
    captured = {}

    async def mock_create_server(factory, host, port, *args, **kwargs):
        captured["host"] = host
        captured["port"] = port
        mock_server = MagicMock()
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
        mock_server.sockets = [mock_socket]
        return mock_server

    mock_reactor.loop.create_server = mock_create_server

    await dcc_connection.listen()
    assert captured["host"] is not None
    assert captured["port"] == 0


@pytest.mark.asyncio
async def test_dcc_connection_listen_ipv6_family(dcc_connection, mock_reactor):
    """listen(ipv6=True) passes AF_INET6 to create_server."""
    import socket

    captured = {}

    async def mock_create_server(factory, host, port, *args, **kwargs):
        captured["family"] = kwargs.get("family")
        mock_server = MagicMock()
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("::1", 54321)
        mock_server.sockets = [mock_socket]
        return mock_server

    mock_reactor.loop.create_server = mock_create_server

    await dcc_connection.listen(addr="::1", ipv6=True)
    assert captured["family"] == socket.AF_INET6


@pytest.mark.asyncio
async def test_dcc_connection_listen_port_range_exact(dcc_connection, mock_reactor):
    """listen() tries exactly the ports in the inclusive range."""
    import irc.client

    attempted: list[int] = []

    async def mock_create_server(factory, host, port, *args, **kwargs):
        attempted.append(port)
        raise OSError("in use")

    mock_reactor.loop.create_server = mock_create_server

    with pytest.raises(irc.client.DCCConnectionError):
        await dcc_connection.listen(port=(15000, 15003))

    assert attempted == [15000, 15001, 15002, 15003]


@pytest.mark.asyncio
async def test_dcc_connection_listen_port_list(dcc_connection, mock_reactor):
    """listen() accepts an explicit iterable of ports."""
    attempted: list[int] = []

    async def mock_create_server(factory, host, port, *args, **kwargs):
        attempted.append(port)
        mock_server = MagicMock()
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = (host, port)
        mock_server.sockets = [mock_socket]
        return mock_server

    mock_reactor.loop.create_server = mock_create_server

    await dcc_connection.listen(port=[16000, 16001])
    assert attempted == [16000]


@pytest.mark.asyncio
async def test_dcc_connection_listen_bind_failure_raises(dcc_connection, mock_reactor):
    """listen() raises DCCConnectionError with the bind error when all ports fail."""
    import irc.client

    async def mock_create_server(factory, host, port, *args, **kwargs):
        raise OSError("address in use")

    mock_reactor.loop.create_server = mock_create_server

    with pytest.raises(irc.client.DCCConnectionError, match="address in use"):
        await dcc_connection.listen(port=15000)


@pytest.mark.asyncio
async def test_dcc_connection_listen_empty_sockname(dcc_connection, mock_reactor):
    """A server socket without a usable sockname leaves address fields as None."""
    async def mock_create_server(factory, host, port, *args, **kwargs):
        mock_server = MagicMock()
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ()
        mock_server.sockets = [mock_socket]
        return mock_server

    mock_reactor.loop.create_server = mock_create_server

    await dcc_connection.listen()

    assert dcc_connection.localaddress is None
    assert dcc_connection.localport is None


@pytest.mark.asyncio
async def test_dcc_connection_listen_short_sockname(dcc_connection, mock_reactor):
    """A malformed 1-element sockname leaves address fields as None."""
    async def mock_create_server(factory, host, port, *args, **kwargs):
        mock_server = MagicMock()
        mock_socket = MagicMock()
        mock_socket.getsockname.return_value = ("127.0.0.1",)
        mock_server.sockets = [mock_socket]
        return mock_server

    mock_reactor.loop.create_server = mock_create_server

    await dcc_connection.listen()

    assert dcc_connection.localaddress is None
    assert dcc_connection.localport is None


def test_dcc_protocol_connection_made_ignores_non_passive(dcc_connection):
    """connection_made on a non-passive connection must not clobber state."""
    protocol = DCCProtocol(dcc_connection, MagicMock())
    dcc_connection.passive = False
    dcc_connection.connected = True
    dcc_connection.transport = MagicMock()

    protocol.connection_made(MagicMock())

    # Non-passive connections manage transport/connected in connect() already.
    assert dcc_connection.transport is not None


def test_dcc_protocol_connection_made_skips_already_connected(dcc_connection, mock_reactor):
    """connection_made on an already-connected passive conn is a no-op."""
    protocol = DCCProtocol(dcc_connection, mock_reactor.loop)
    dcc_connection.passive = True
    dcc_connection.connected = True
    original_transport = MagicMock()
    dcc_connection.transport = original_transport

    protocol.connection_made(MagicMock())

    assert dcc_connection.transport is original_transport


def test_dcc_protocol_connection_made_sets_missing_transport(dcc_connection, mock_reactor):
    """connection_made assigns the transport on a connection lacking one."""
    protocol = DCCProtocol(dcc_connection, mock_reactor.loop)
    dcc_connection.passive = False
    dcc_connection.connected = True
    if hasattr(dcc_connection, "transport"):
        del dcc_connection.transport

    transport = MagicMock()
    protocol.connection_made(transport)

    assert dcc_connection.transport is transport


@pytest.mark.asyncio
async def test_aio_reactor_dcc_default_type():
    """AioReactor.dcc() defaults to a chat connection."""
    loop = asyncio.get_running_loop()
    reactor = AioReactor()
    reactor.loop = loop
    reactor.mutex = MagicMock()
    reactor.mutex.__enter__ = MagicMock()
    reactor.mutex.__exit__ = MagicMock()
    reactor.connections = []

    dcc = reactor.dcc()

    assert dcc.dcctype == "chat"


@pytest.mark.asyncio
async def test_aio_reactor_dcc_chat():
    """Test AioReactor.dcc() with chat type."""
    loop = asyncio.get_running_loop()
    reactor = AioReactor()
    reactor.loop = loop
    reactor.mutex = MagicMock()
    reactor.mutex.__enter__ = MagicMock()
    reactor.mutex.__exit__ = MagicMock()
    reactor.connections = []

    dcc = reactor.dcc("chat")

    assert isinstance(dcc, AioDCCConnection)
    assert dcc.dcctype == "chat"


