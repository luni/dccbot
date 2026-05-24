import asyncio
import logging
import socket
from asyncio.transports import Transport

import irc.client
import irc.client_aio
import irc.connection
from jaraco.stream import buffer

log = logging.getLogger(__name__)


class DCCProtocol(irc.client_aio.IrcProtocol):
    """Subclass of IrcProtocol handling DCC connections.

    Handles passive DCC incoming connections via connection_made.
    """

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Handle connection made for passive DCC connections."""
        if self.connection.passive and not self.connection.connected:
            self.connection.transport = transport
            self.connection.connected = True
            self.connection.peeraddress, self.connection.peerport = transport.get_extra_info('peername')
            log.debug("DCC connection from %s:%d", self.connection.peeraddress, self.connection.peerport)
            self.connection.reactor._handle_event(
                self.connection, irc.client.Event("dcc_connect", self.connection.peeraddress, None, None)
            )
            if hasattr(self.connection, 'server') and self.connection.server:
                self.connection.server.close()
            return

        # For active connections, ensure transport is set if not already
        if not getattr(self.connection, 'transport', None):
            self.connection.transport = transport


class NonStrictDecodingLineBuffer(buffer.DecodingLineBuffer):
    """A subclass of DecodingLineBuffer that decodes the line using the replace error handler.

    This class is used by AioDCCConnection to decode the incoming data. It
    decodes the line using the replace error handler, which replaces invalid
    characters with a replacement marker (such as '?').

    Attributes:
        errors (str): The error handler to use when decoding the line.

    """

    errors = "replace"


class NonStrictAioConnection(irc.client_aio.AioConnection):
    """A subclass of AioConnection that uses NonStrictDecodingLineBuffer for incoming data.

    The NonStrictDecodingLineBuffer is a subclass of DecodingLineBuffer that
    decodes the line using the replace error handler. This means that invalid
    characters in the incoming data will be replaced with a replacement marker
    (such as '?') instead of raising an error.
    """

    buffer_class = NonStrictDecodingLineBuffer


class AioDCCConnection(irc.client.DCCConnection):
    """A subclass of DCCConnection that handles DCC connections with asyncio.

    Attributes:
        reactor: The reactor that created this object.
        buffer_class: The buffer class to use for incoming data.
        passive: Whether this is a passive connection.
        peeraddress: The address of the peer.
        peerport: The port of the peer.

    """

    reactor: "AioReactor"
    buffer_class = NonStrictDecodingLineBuffer

    protocol_class = DCCProtocol
    transport: Transport
    protocol: DCCProtocol
    socket: None
    connected: bool
    peeraddress: str
    peerport: int

    async def connect(  # type: ignore
        self, address: str, port: int, connect_factory: irc.connection.AioFactory = irc.connection.AioFactory(), transfer_item: dict | None = None
    ) -> "AioDCCConnection":
        """Connect/reconnect to a DCC peer.

        Args:
            address: The address of the peer.
            port: The port to connect to.
            connect_factory: A callable that takes the event loop and the
              server address, and returns a connection (with a socket interface)
            transfer_item: The transfer item dictionary to set status after connection is established

        Returns:
            The DCCConnection object.

        """
        self.peeraddress = address
        self.peerport = port
        self.handlers = {}
        self.buffer = self.buffer_class()

        self.connect_factory = connect_factory
        protocol_instance = self.protocol_class(self, self.reactor.loop)
        try:
            connection = self.connect_factory(protocol_instance, (self.peeraddress, self.peerport))
            transport, protocol = await connection
        except Exception as e:
            log.error("Connection error to %s:%s: %s", self.peeraddress, self.peerport, e)
            if transfer_item:
                transfer_item["error"] = str(e)
                transfer_item["status"] = "error"
            self.connected = False
            return self

        self.transport = transport
        self.protocol = protocol

        self.connected = True
        self.reactor._on_connect(self.protocol, self.transport)
        return self

    async def listen(
        self,
        addr: str | tuple[str, int] | None = None,
        port: int | tuple[int, int] | list[int] | None = None,
        ipv6: bool = False,
    ) -> "AioDCCConnection":  # type: ignore
        """Wait for a connection/reconnection from a DCC peer.

        Returns the DCCConnection object.

        The local IP address and port are available as
        self.localaddress and self.localport. After connection from a
        peer, the peer address and port are available as
        self.peeraddress and self.peerport.

        Args:
            addr: Host string or (host, port) tuple to bind to.
                  If a tuple, the port is only used if `port` is None.
            port: Port to listen on. Can be an int, a (min, max) tuple
                  to try a range, or a list of ports to try in order.
                  Overrides the port in `addr` if both are provided.
            ipv6: Use IPv6 if True.

        """
        self.passive = True
        self.handlers = {}
        self.buffer = self.buffer_class()

        # Resolve host and default port from addr
        if addr is None:
            host = socket.gethostbyname(socket.gethostname())
            addr_port = 0
        elif isinstance(addr, str):
            host = addr
            addr_port = 0
        else:
            host, addr_port = addr

        # port parameter overrides addr port if specified
        if port is None:
            port = addr_port

        def factory() -> DCCProtocol:
            return self.protocol_class(self, self.reactor.loop)

        family = socket.AF_INET6 if ipv6 else socket.AF_INET

        # Build iterable of ports to try
        if isinstance(port, int):
            ports = [port]
        elif isinstance(port, tuple):
            ports = range(port[0], port[1] + 1)
        else:
            ports = port  # assume list/iterable

        last_error = None
        for try_port in ports:
            try:
                self.server = await self.reactor.loop.create_server(
                    factory, host, try_port, family=family
                )
                break
            except OSError as ex:
                last_error = ex
                continue
        else:
            raise irc.client.DCCConnectionError(f"Couldn't bind socket: {last_error}") from last_error

        # Get the actual bound address and port
        socket_obj = self.server.sockets[0]
        self.localaddress, self.localport = socket_obj.getsockname()

        return self

    def disconnect(self, message: str = "") -> None:
        """Hang up the connection and close the object.

        Args:
            message: Quit message.

        """
        try:
            del self.connected
        except AttributeError:
            return

        try:
            if hasattr(self, 'server') and self.server:
                self.server.close()
        except AttributeError:
            pass

        try:
            self.transport.close()
        except AttributeError:
            pass

        self.reactor._handle_event(
            self, irc.client.Event("dcc_disconnect", self.peeraddress, "", [message])
        )
        self.reactor._remove_connection(self)

    def process_data(self, new_data: bytes) -> None:  # type: ignore
        """Handle incoming data from the `DCCProtocol` connection.

        Args:
            new_data: The data to process.

        """
        if self.dcctype == "chat":
            self.buffer.feed(new_data)

            chunks = list(self.buffer)

            if len(self.buffer) > 2**14:
                # Bad peer! Naughty peer!
                log.info("Received >16k from a peer without a newline; disconnecting.")
                self.disconnect()
                return
        else:
            chunks = [new_data]

        command = "dccmsg"
        prefix = self.peeraddress
        target = None
        for chunk in chunks:
            log.debug("FROM PEER: %s", chunk)
            arguments = [chunk]
            log.debug(
                "command: %s, source: %s, target: %s, arguments: %s",
                command,
                prefix,
                target,
                arguments,
            )
            event = irc.client.Event(command, prefix, target, arguments)
            self.reactor._handle_event(self, event)

    def privmsg(self, text: str) -> None:
        """Send text to DCC peer.

        The text will be padded with a newline if it's a DCC CHAT session.
        """
        if self.dcctype == 'chat':
            text += '\n'
        return self.send_bytes(self.encode(text))

    def send_bytes(self, data: bytes) -> None:
        """Send data to DCC peer.

        Args:
            data: The data to send.

        """
        try:
            self.transport.write(data)
            log.debug("TO PEER: %r\n", data)
        except (OSError, AttributeError):
            self.disconnect("Connection reset by peer.")


class AioReactor(irc.client_aio.AioReactor):
    """Asynchronous IRC client reactor with DCC support.

    This reactor is used by the IRC client to handle incoming and outgoing
    data. It also provides the ability to create DCC connections.

    Attributes:
        dcc_connection_class: The class to use for creating DCCConnection
            objects. Defaults to AioDCCConnection.

    """

    dcc_connection_class = AioDCCConnection

    def dcc(self, dcctype: str = "chat") -> AioDCCConnection:
        """Create a and return a DCCConnection object.

        Args:
            dcctype (str): The type of DCC connection to create. Defaults to
                "chat". If "chat", incoming data will be split in
                newline-separated chunks. If "raw", incoming data is not
                touched.

        Returns:
            dccbot.aiodcc.AioDCCConnection: The created DCCConnection object.

        """
        with self.mutex:
            conn = self.dcc_connection_class(self, dcctype)
            self.connections.append(conn)
        return conn
