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
    loop = asyncio.get_event_loop()
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


def test_dcc_connection_listen_not_implemented(dcc_connection):
    """Test that listen() raises NotImplementedError."""
    with pytest.raises(NotImplementedError):
        asyncio.run(dcc_connection.listen())


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


@pytest.mark.asyncio
async def test_dcc_connection_process_data_passive_not_implemented(mock_reactor):
    """Test that passive DCC raises NotImplementedError."""
    dcc_connection = AioDCCConnection(mock_reactor, "raw")
    dcc_connection.passive = True
    dcc_connection.connected = False

    with pytest.raises(NotImplementedError):
        dcc_connection.process_data(b"test")
