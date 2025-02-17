import irc.client
import irc.connection
import irc.client_aio
from jaraco.stream import buffer
import logging


log = logging.getLogger(__name__)


class DCCProtocol(irc.client_aio.IrcProtocol):
    pass


class AioDCCConnection(irc.client.DCCConnection):
    rector: "AioReactor"
    buffer_class = buffer.DecodingLineBuffer

    protocol_class = DCCProtocol
    transport: irc.connection.AioFactory
    protocol: DCCProtocol
    socket: None
    connected: bool
    passive: bool
    peeraddress: str
    peerport: int

    async def connect(self, address: str, port: int, connect_factory: irc.connection.AioFactory = irc.connection.AioFactory()):
        """Connect/reconnect to a DCC peer.

        Arguments:
            address -- Host/IP address of the peer.
            port -- The port number to connect to.
            connect_factory -- A callable that takes the event loop and the
              server address, and returns a connection (with a socket interface)

        Returns the DCCConnection object.
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
            self.connected = False
            return self

        self.transport = transport
        self.protocol = protocol

        self.connected = True
        self.reactor._on_connect(self.protocol, self.transport)
        return self

    # TODO: implement listen() in asyncio way
    async def listen(self, addr=None):
        """Wait for a connection/reconnection from a DCC peer.

        Returns the DCCConnection object.

        The local IP address and port are available as
        self.peeraddress and self.peerport.
        """

        raise NotImplementedError()

    def disconnect(self, message: str = ""):
        """Hang up the connection and close the object.

        Arguments:

            message -- Quit message.
        """
        try:
            del self.connected
        except AttributeError:
            return

        self.transport.close()

        self.reactor._handle_event(
            self, irc.client.Event("dcc_disconnect", self.peeraddress, "", [message])
        )
        self.reactor._remove_connection(self)

    def process_data(self, new_data: bytes):
        """
        handles incoming data from the `DCCProtocol` connection.
        """

        if self.passive and not self.connected:
            raise NotImplementedError()
            # TODO: implement passive DCC connection

        if self.dcctype == "chat":
            self.buffer.feed(new_data)

            chunks = list(self.buffer)

            if len(self.buffer) > 2**14:
                # Bad peer! Naughty peer!
                log.info(
                    "Received >16k from a peer without a newline; " "disconnecting."
                )
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

    def send_bytes(self, bytes):
        """
        Send data to DCC peer.
        """
        try:
            self.transport.write(bytes)
            log.debug("TO PEER: %r\n", bytes)
        except OSError:
            self.disconnect("Connection reset by peer.")


class AioReactor(irc.client_aio.AioReactor):
    dcc_connection_class = AioDCCConnection

    def dcc(self, dcctype="chat"):
        """Creates and returns a DCCConnection object.

        Arguments:

            dcctype -- "chat" for DCC CHAT connections or "raw" for
                       DCC SEND (or other DCC types). If "chat",
                       incoming data will be split in newline-separated
                       chunks. If "raw", incoming data is not touched.
        """
        with self.mutex:
            conn = self.dcc_connection_class(self, dcctype)
            self.connections.append(conn)
        return conn
