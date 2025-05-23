import asyncio
import ipaddress
import logging
import os
import random
import re
import shlex
import ssl
import string
import struct
import time
import uuid
from typing import TYPE_CHECKING, Any

import irc.client
import irc.client_aio
import magic
from irc.client_aio import AioSimpleIRCClient
from irc.connection import AioFactory

from dccbot.aiodcc import AioDCCConnection, AioReactor
from dccbot.aiodcc import NonStrictAioConnection as AioConnection

if TYPE_CHECKING:
    from dccbot.manager import IRCBotManager

logger = logging.getLogger(__name__)


class IRCBot(AioSimpleIRCClient):
    """Main class for the IRC bot.

    Attributes:
        server: The server to connect to.
        server_config: The configuration for the server. Should contain keys:
            - nick: The IRC nickname to use.
            - nickserv_password: The password for nickserv.
            - use_tls: Whether to use TLS for the connection.
            - random_nick: Whether to generate a random nickname.
            - channels: A list of channels to join.
            - port: The port to connect to. Defaults to 6667.
        download_path: The path to download files to.
        allowed_mimetypes: A list of allowed mimetypes for DCC transfers.
        max_file_size: The maximum size of a DCC transfer.
        bot_channel_map: Map of bot channels to map to channels on the server.
        resume_queue: Queue of resumable transfers.
        command_queue: Queue of commands to send to the server.
        loop: The asyncio event loop.
        last_active: The last time the bot was active.
        joined_channels: The channels the bot is joined to.
        current_transfers: The current DCC transfers.
        banned_channels: The channels the bot is banned from.
        connection: The connection to the server.
        bot_manager: The parent IRCBotManager object.
        authenticated_event: Event to signal when the bot is authenticated.
        authenticated: Whether the bot is authenticated.

    """

    reactor_class = AioReactor
    download_path: str
    allowed_mimetypes: list[str] | None
    max_file_size: int
    bot_channel_map: dict[str, set[str]]
    resume_queue: dict[str, list[tuple[str, int, str, str, int, int, bool, bool, float]]]
    command_queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop
    last_active: float
    joined_channels: dict[str, float]
    current_transfers: dict[AioDCCConnection, dict[str, Any]]
    banned_channels: set[str]
    connection: AioConnection
    bot_manager: "IRCBotManager"
    authenticated_event: asyncio.Event
    authenticated: bool
    config: dict

    def __init__(
        self, server: str, server_config: dict, download_path: str, allowed_mimetypes: list[str] | None, max_file_size: int, bot_manager: "IRCBotManager"
    ) -> None:
        """Initialize an IRCBot object.

        Args:
            server: The server to connect to.
            server_config: The configuration for the server. Should contain keys:
                - nick: The IRC nickname to use.
                - nickserv_password: The password for nickserv.
                - use_tls: Whether to use TLS for the connection.
                - random_nick: Whether to generate a random nickname.
                - channels: A list of channels to join.
                - port: The port to connect to. Defaults to 6667.
            download_path: The path to download files to.
            allowed_mimetypes: A list of allowed mimetypes for DCC transfers.
            max_file_size: The maximum size of a DCC transfer.
            bot_manager: The parent IRCBotManager object.

        """
        super().__init__()
        self.server = server
        self.server_config = server_config
        if server_config.get("random_nick", False):
            self.nick = self._generate_random_nick(server_config.get("nick", "dccbot"))
        else:
            self.nick = server_config.get("nick", "dccbot")

        self.download_path = download_path
        self.allowed_mimetypes = allowed_mimetypes
        self.max_file_size = max_file_size
        self.joined_channels = {}  # (channel) -> last active time
        self.current_transfers = {}  # track active DCC connections
        self.banned_channels = set()
        self.resume_queue = {}
        self.command_queue = asyncio.Queue()
        self.mime_checker = magic.Magic(mime=True)
        self.loop = asyncio.get_event_loop()  # Ensure the loop is set
        self.last_active = time.time()
        self.bot_channel_map = {}
        self.bot_manager = bot_manager
        self.authenticated_event = asyncio.Event()
        self.authenticated = False
        self.config = bot_manager.config

    @staticmethod
    def get_version() -> str:
        """Return the bot version.

        Used when answering a CTCP VERSION request.

        """
        return "dccbot 1.0"

    @staticmethod
    def _generate_random_nick(base_nick: str) -> str:
        """Generate a random IRC nick by appending a 3-digit random number to the given base nick.

        Args:
            base_nick (str): The base nick to use for generating the full nick.

        Returns:
            str: The full nick with a random 3-digit suffix.

        """
        random_suffix = "".join(random.choices(string.digits, k=3))  # nosec
        return f"{base_nick}{random_suffix}"

    async def connect(self) -> None:  # type: ignore
        """Establish a connection to the IRC server.

        If a TLS connection is configured (``use_tls=True``), the connection
        will be established on port 6697. Otherwise, the connection will be
        established on port 6667.

        The connection is established using the ``AioConnection`` class
        from ``irc.client_aio``. The connection is assigned to the
        ``connection`` attribute of the bot.

        If the connection fails, an error message will be logged.
        """
        try:
            self.connection = AioConnection(self.reactor)
            connect_factory = None

            if self.server_config.get("use_tls", False):
                # Initialize AioConnection with the custom connect_factory
                if not self.server_config.get("verify_ssl", True):
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                else:
                    ssl_context = True
                connect_factory = AioFactory(ssl=ssl_context)
                port = self.server_config.get("port", 6697)
            else:
                connect_factory = AioFactory()
                port = self.server_config.get("port", 6667)

            await self.connection.connect(self.server, port, self.nick, connect_factory=connect_factory)
            logger.info("Connecting to server: %s with nick: %s", self.server, self.nick)
        except Exception as e:
            logger.error("Connection error to %s: %s", self.server, e)

    async def disconnect(self, reason: str | None = None) -> None:
        """Disconnect the bot from the IRC server.

        Args:
            reason (str): Optional quit message to send to the server.

        """
        self.connection.disconnect(reason or "")
        logger.info("Disconnected from server %s (%s)", self.server, reason)

    async def join_channel(self, channel: str) -> None:
        """Join the specified channel.

        Args:
            channel (str): The channel to join.

        If the channel is empty or the bot is already in the channel,
        this function does nothing and returns.

        """
        if not channel or channel in self.joined_channels:
            return

        self.connection.join(channel)
        logger.info("Try to join channel: %s", channel)

    async def part_channel(self, channel: str, reason: str | None = None) -> None:
        """Part the specified channel.

        Args:
            channel (str): The channel to part.
            reason (str): Optional part message to send to the server.

        """
        if channel not in self.joined_channels:
            # If the channel is empty or the bot is not in the channel, do nothing
            return

        self.connection.part(channel, reason or "")
        logger.info("Parted channel: %s (%s)", channel, reason)
        self.last_active = time.time()
        del self.joined_channels[channel]

    async def queue_command(self, data: dict) -> None:
        """Queue a command to be processed by the bot.

        Args:
            data (dict): The command to be processed. The command should be a dictionary with the following keys:
                - command (str): The command to be processed. The command can be any of the following:
                    - part: Part the channel.
                    - join: Join the channel.
                    - send: Send a message to the channel.
                    - quit: Quit the server.
                - channels (list of str): The channels to be processed. The channels are only required if the command is part, join, or send.
                - reason (str): The reason for the command. The reason is only required if the command is part or quit.

        """
        await self.command_queue.put(data)
        logger.debug("Queued command: %s", data)

    async def _handle_authentication(self) -> None:
        """Handle NickServ authentication if required."""
        if self.server_config.get("nickserv_password") and self.authenticated is False:
            logging.debug("Waiting for NickServ authentication")
            try:
                await asyncio.wait_for(self.authenticated_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                logger.error("Timed out waiting for NickServ authentication")

    async def _join_channels(self, channels: list[str]) -> None:
        """Join specified channels and update waiting channels list.

        Args:
            channels (list of str): The channels to join.

        """
        waiting_channels: list[str] = []
        for channel in channels:
            await self.join_channel(channel)
            waiting_channels.append(channel)
            if channel in self.server_config.get("also_join", {}):
                for also_join_channel in self.server_config["also_join"][channel]:
                    await self.join_channel(also_join_channel)
                    waiting_channels.append(also_join_channel)

        retry = 0
        while retry < 10 and waiting_channels:
            for channel in list(waiting_channels):
                if channel in self.joined_channels:
                    waiting_channels.remove(channel)

            await asyncio.sleep(1)
            retry += 1

        if waiting_channels:
            logger.warning("Failed to join channels %s after 10 seconds", ", ".join(waiting_channels))

    def _update_channel_mapping(self, user: str, channels: list[str]) -> None:
        """Update bot's channel mapping for a user.

        Args:
            user (str): The user to update the channel mapping for.
            channels (list of str): The channels to add to the user's channel mapping.

        """
        if user not in self.bot_channel_map:
            self.bot_channel_map[user] = set(channels)
        else:
            self.bot_channel_map[user] |= set(channels)

        if user in self.bot_channel_map:
            for channel in self.bot_channel_map[user]:
                self.joined_channels[channel] = time.time()

    async def _handle_send_command(self, data: dict[str, Any]) -> None:
        """Handle send command by joining channels and sending messages.

        This method processes a send command by:
        1. Joining specified channels
        2. Waiting for channel joins
        3. Sending the message
        4. Updating channel mapping

        Args:
            data: Dictionary containing command data with keys:
                - channels: List of channels to join
                - user: User to send message to
                - message: Message to send

        """
        if not data.get("user") or not data.get("message"):
            return

        if data.get("channels"):
            await self._join_channels(data["channels"])

        try:
            self.connection.privmsg(data["user"], data["message"])
            logger.info("Sent message to %s: %s", data["user"], data["message"])
        except Exception as e:
            logger.error("Failed to send message to %s: %s", data["user"], e)

        if data.get("channels"):
            self._update_channel_mapping(data["user"], data["channels"])

    async def _handle_part_command(self, data: dict[str, Any]) -> None:
        """Handle part command by parting specified channels.

        Args:
            data: Dictionary containing command data with keys:
                - channels: List of channels to part
                - reason: Optional reason for parting

        """
        if data.get("channels"):
            for channel in data["channels"]:
                await self.part_channel(channel, data.get("reason"))

    async def process_command_queue(self) -> None:
        """Process commands from the command queue.

        This function runs an infinite loop that checks the command queue for new commands.
        It will process commands according to their type (send, join, or part).

        """
        await self._handle_authentication()

        for channel in self.server_config.get("channels", []):
            asyncio.create_task(self.join_channel(channel))

        while True:
            data: dict[str, Any] = await self.command_queue.get()
            self.last_active = time.time()

            if not data:
                continue

            if data["command"] == "send":
                await self._handle_send_command(data)
            elif data["command"] == "part":
                await self._handle_part_command(data)

    def on_welcome(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Process operations after receiving the welcome message from the server.

        If the bot is configured to authenticate with NickServ, this method sends the
        IDENTIFY command to NickServ.

        Also joins channels, of the bot is configured to join channels.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        logger.info("Connected to server: %s", self.server)

        # Authenticate with NickServ
        if self.server_config.get("nickserv_password"):
            self.connection.privmsg("NickServ", f"IDENTIFY {self.server_config['nickserv_password']}")
            logger.info("Sent NickServ IDENTIFY command")

        # Start processing the message queue
        asyncio.create_task(self.process_command_queue())

    def on_nosuchnick(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Show an error message when the bot receives a NOSUCHNICK message from the server.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """

    def on_bannedfromchan(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Add the channel to the list of banned channels and remove it from the list of joined channels.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        logger.info("Banned from channel %s: %s", event.target, event.arguments[0])
        channel_name = event.arguments[0].lower()
        self.banned_channels.add(channel_name)

    def on_nochanmodes(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Process operations after receiving a NOCHANMODES message from the server.

        If the bot is not allowed to join (because of a channel mode), remove it from the list of joined channels.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        logger.info("Not allowed to join channel %s: %s", event.arguments[0], event.arguments[1])
        channel_name = event.arguments[0].lower()
        if channel_name in self.joined_channels:
            logger.info("Removed from channel %s: %s", event.target, event.arguments)
            del self.joined_channels[channel_name]

    def on_loggedin(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Process operations after receiving a LOGGEDIN message from the server.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        logger.info(event.arguments)
        self.authenticated_event.set()
        self.authenticated = True

    def on_part(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Process operations after receiving a PART message from the server.

        If the bot was parted from the channel, remove it from the list of joined channels.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        if event.source.nick != self.nick:
            return

        channel_name = event.target.lower()
        if channel_name in self.joined_channels:
            logger.info("Left channel %s: %s", event.target, event.arguments)
            del self.joined_channels[channel_name]

    def on_join(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Process operations after receiving a JOIN message from the server.

        If the bot was joined the channel, add it to the list of joined channels.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        if event.source.nick != self.nick:
            return

        channel_name = event.target.lower()
        if channel_name not in self.joined_channels:
            logger.info("Joined channel %s: %s", event.target, event.arguments)
            self.joined_channels[channel_name] = time.time()
            self.banned_channels.discard(channel_name)

    def on_kick(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Process operations after receiving a KICK message from the server.

        If the bot was kicked from the channel, remove it from the list of joined channels.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        logger.info("Kicked from channel %s: %s", event.target, event.arguments)
        channel_name = event.target.lower()
        if channel_name in self.joined_channels:
            del self.joined_channels[channel_name]

    @staticmethod
    def is_valid_filename(path: str, filename: str) -> bool:
        """Check if a given filename is valid.

        A filename is considered valid if:

        1. It is not empty
        2. It does not contain any invalid characters (e.g. slash, backslash, :, *, ?, ", <, >, |)
        3. It does not contain any directory separators (e.g. slash, backslash)
        4. It is an absolute path
        5. It is not outside of the given path

        This function is used to validate filenames when downloading files from IRC.

        Args:
            path (str): The path to the directory where the file will be saved.
            filename (str): The name of the file.

        Returns:
            bool: True if the filename is valid, False if not.

        """
        if not filename:
            return False

        file_path = os.path.join(path, filename)

        if re.search(r"[/\\:\*?\"<>\|]", filename):  # Check for invalid characters
            return False

        # Check if the file is within the given path
        if not os.path.abspath(file_path).startswith(path):
            return False

        return True

    def on_dcc_accept(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Handle DCC ACCEPT command.

        This method handles the DCC ACCEPT command, which is sent by the server to the bot when it
        should accept a DCC file transfer that was previously paused.

        If the DCC ACCEPT command is not in the resume queue, it is ignored.

        The method also checks the validity of the port and resume position in the command.
        If they are invalid, the method returns without doing anything else.

        If the DCC ACCEPT command is valid, the method removes the item from the resume queue and
        initializes a new DCC connection for the file transfer.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        if event.source.nick not in self.resume_queue:
            logger.warning("DCC ACCEPT not in queue: %s", event)
            return

        f = re.search(r"(\d+) (\d+)$", event.arguments[1])
        if not f:
            logger.warning("Invalid DCC ACCEPT command: %s", event)
            return

        try:
            peer_port = int(f.group(1))
            resume_position = int(f.group(2))

            if peer_port < 1024 or peer_port > 65535:
                logger.warning("Invalid DCC SEND command (invalid port): %s", event.arguments)
                return

            if resume_position < 1:
                logger.warning("Invalid DCC SEND command (invalid resume_position): %s", event.arguments)
                return
        except ValueError:
            logger.warning("Invalid DCC SEND command (invalid size or port): %s", event.arguments)
            return

        for item in self.resume_queue[event.source.nick]:
            if peer_port != item[1] or resume_position != item[5]:
                continue

            self.resume_queue[event.source.nick].remove(item)
            break
        else:
            logger.warning("DCC ACCEPT command for unknown file: %s", event)
            return

        if not self.resume_queue[event.source.nick]:
            del self.resume_queue[event.source.nick]

        self.init_dcc_connection(event.source.nick, item[0], peer_port, item[2], item[3], item[4], resume_position, item[6], item[7])

    def on_dcc_send(self, connection: AioConnection, event: irc.client_aio.Event, use_ssl: bool) -> None:
        """Handle DCC SEND command.

        The bot responds with a DCC RESUME command if the file already exists and the local file size is smaller than the remote file size.
        If the local file size is larger than the remote file size, the bot rejects the file.
        If the local file size is equal to the remote file size, the bot marks the file as completed and doesn't send a DCC RESUME command.
        The bot also stores the file information in the `resume_queue` if the file is not completed.
        If the file is completed, the bot removes the file information from the `resume_queue`.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.
            use_ssl (bool): A boolean indicating whether to use SSL.

        """
        payload = event.arguments[1]
        parts = shlex.split(payload)

        if len(parts) < 5:
            logger.warning("Invalid DCC SEND command (not enough arguments): %s", event.arguments)
            return

        filename, peer_address, peer_port, size = parts[1:5]

        # handle v6
        if ":" in peer_address:
            # Validate the IP address
            try:
                ipaddress.ip_address(peer_address)
            except ValueError:
                logger.warning("Rejected %s: Invalid IP address %s", filename, peer_address)
                return
        else:
            try:
                # Convert the IP address to a quad-dotted form
                peer_address = irc.client.ip_numstr_to_quad(peer_address)
            except ValueError:
                logger.warning("Rejected %s: Invalid IP address %s", filename, peer_address)
                return

        if ipaddress.ip_address(peer_address).is_private and not self.config.get("allow_private_ips"):
            logger.warning("Rejected %s: Private IP address %s", filename, peer_address)
            return

        # validate file name
        if not self.is_valid_filename(self.download_path, filename):
            logger.warning("Invalid DCC SEND command (file name contains invalid characters): %s", filename)
            return

        try:
            size = int(size)
            peer_port = int(peer_port)

            if peer_port == 0:
                logger.warning("Passive DCC transfers are not supported yet.")
                return

            if peer_port < 1 or peer_port > 65535:
                logger.warning("Invalid DCC SEND command (invalid port)")
                return

            if size < 1:
                logger.warning("Invalid DCC SEND command (invalid size)")
                return
        except ValueError:
            logger.warning("Invalid DCC SEND command (invalid size or port)")
            return

        if size > self.max_file_size:
            logger.warning("Rejected %s: File size exceeds limit (%d > %d)", filename, size, self.max_file_size)
            return

        if size < 1:
            logger.warning("Rejected %s: File size is too small (%d)", filename, size)
            return

        # check if transfer for same file already running
        for item in self.bot_manager.transfers.get(filename, []):
            if item["size"] == size and item.get("connected", False):
                logger.warning("Rejected %s: Download of file already in progress", filename)
                return

        local_download_path = os.path.join(self.download_path, filename)
        local_files = [local_download_path]
        if self.config.get("incomplete_suffix"):
            local_files.append(local_download_path + self.config["incomplete_suffix"])
            local_download_path += self.config["incomplete_suffix"]

        local_size = 0
        completed = False
        for download_path in local_files:
            if os.path.exists(download_path):
                local_size = os.path.getsize(download_path)
                if local_size > size:
                    logger.warning("Rejected %s: Local file larger then remote file (%d > %d)", filename, local_size, size)
                    return

                if local_size == size:
                    completed = True
                    logger.info("%s: File already completed, send resume command for last 4096 to complete transfer request.", filename)
                    local_size -= 4096

                logger.info("Send DCC RESUME %s starting at %d bytes", filename, local_size)
                self.connection.ctcp_reply(
                    event.source.nick, " ".join(["DCC", "RESUME", '"' + filename.replace('"', "") + '"', str(peer_port), str(local_size)])
                )

                if event.source.nick not in self.resume_queue:
                    self.resume_queue[event.source.nick] = []

                self.resume_queue[event.source.nick].append((
                    peer_address,
                    peer_port,
                    filename,
                    download_path,
                    size,
                    local_size,
                    use_ssl,
                    completed,
                    time.time(),
                ))
                return

        self.init_dcc_connection(event.source.nick, peer_address, peer_port, filename, local_files[-1], size, local_size, use_ssl, completed)

    def on_ctcp(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Handle CTCP messages.

        This method handles two types of CTCP messages: DCC and PING.

        The DCC message is sent by the server to the bot when the bot should
        accept a DCC file transfer. The message contains the file name, peer
        address, peer port, and file size.

        The PING message is sent by the server to the bot when the server wants
        the bot to respond with a CTCP PONG message. This is used to keep the
        connection alive.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        self.last_active = time.time()

        # Only handle DCC messages
        if event.arguments[0] != "DCC":
            return self.on_privmsg(connection, event)

        if not event.arguments or len(event.arguments) < 2:
            logger.warning("Invalid DCC event: %s", event)
            return

        # update timeout
        if event.source.nick.lower() in self.bot_channel_map:
            for channel in self.bot_channel_map[event.source.nick.lower()]:
                self.joined_channels[channel] = time.time()

        if event.arguments[1].startswith("ACCEPT "):
            return self.on_dcc_accept(connection, event)

        if event.arguments[1].startswith("SEND ") or event.arguments[1].startswith("SSEND "):
            use_ssl = False
            if event.arguments[1].startswith("SSEND "):
                use_ssl = True
            return self.on_dcc_send(connection, event, use_ssl)

        logger.warning("Unknown DCC event: %s", event)

    def init_dcc_connection(
        self,
        nick: str,
        peer_address: str,
        peer_port: int,
        filename: str,
        download_path: str,
        size: int,
        offset: int | None = None,
        use_ssl: bool | None = False,
        completed: bool | None = False,
    ) -> None:
        """Initialize a DCC connection to a peer.

        This method sets up a DCC connection to the peer, creates the
        file to receive the data and stores the information in the
        `current_transfers` dictionary.

        Args:
            nick (str): The name of the peer.
            peer_address (str): The address of the peer.
            peer_port (int): The port of the peer.
            filename (str): The name of the file to receive.
            download_path (str): The path + filename to the file to store.
            size (int): The size of the file.
            offset (int): The offset of the file to resume from.
            use_ssl (bool): Whether to use SSL.
            completed (bool): Whether the file transfer is already completed.

        """
        dcc_msg = "Receiving file via DCC" if not use_ssl else "Receiving file via SSL DCC"
        logger.info("[%s] %s %s from %s:%d, size: %d bytes", nick, dcc_msg, filename, peer_address, peer_port, size)

        # Convert the port to an integer
        logger.info("[%s] Connecting to %s:%s", nick, peer_address, peer_port)

        # Create a new DCC connection
        dcc: AioDCCConnection = self.dcc("raw")  # type: ignore

        connect_factory = None
        if use_ssl:
            # Create a new SSL context without hostname verification and disable certificate validation
            # This is necessary because the server does not have a valid certificate
            # SSL is only used for encryption, not for authentication of the sender
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connect_factory = AioFactory(ssl=ssl_context)
        else:
            connect_factory = AioFactory()

        now = time.time()

        transfer_item = {
            "id": uuid.uuid4().hex,
            "nick": nick,
            "server": self.server,
            "peer_address": peer_address,
            "peer_port": peer_port,
            "file_path": download_path,
            "filename": filename,
            "start_time": now,
            "bytes_received": 0,
            "offset": offset,
            "size": size,
            "ssl": use_ssl,
            "percent": 0,
            "last_progress_update": 0,
            "last_progress_bytes_received": 0,
            "completed": completed,
            "status": "started",
        }

        # Store the information about the file transfer
        # check if we already have an entry by the CTCP message from XDCC bot
        for item in self.bot_manager.transfers.get(filename, []):
            if item.get("peer_address") is None and item["start_time"] >= now - 30 and item["nick"] == nick and item["server"] == self.server:
                item.update(transfer_item)
                transfer_item = item
                break
        else:
            # nothing found, add new entry
            if not self.bot_manager.transfers.get(filename):
                self.bot_manager.transfers[filename] = []

            self.bot_manager.transfers[filename].append(transfer_item)

        self.current_transfers[dcc] = transfer_item

        # Schedule the connection to be established
        self.loop.create_task(dcc.connect(peer_address, peer_port, connect_factory=connect_factory, transfer_item=transfer_item))

    def on_dccmsg(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Handle DCC messages.

        This method handles the DCC message, which is sent by the server to the bot when the bot
        should receive a DCC file transfer. The message contains the chunk of data from the file.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        dcc = connection
        if dcc not in self.current_transfers:
            logger.debug("Received DCC message from unknown connection")
            return

        transfer = self.current_transfers[dcc]
        transfer["connected"] = True
        transfer["status"] = "in_progress"
        data = event.arguments[0]

        # If file is already completed, ignore data
        if not transfer["completed"]:
            now = time.time()

            # update timeout
            if transfer["nick"].lower() in self.bot_channel_map:
                for channel in self.bot_channel_map[transfer["nick"].lower()]:
                    self.joined_channels[channel] = now

            percent = int(100 * (transfer["bytes_received"] + transfer["offset"]) / transfer["size"])
            if transfer["percent"] + 10 <= percent or now - transfer["last_progress_update"] >= 5:
                transfer["percent"] = percent
                elapsed_time = now - transfer["start_time"]
                transfer_rate_avg = (transfer["bytes_received"] / elapsed_time) / 1024 if elapsed_time > 0 else 0

                elapsed_time = now - transfer["last_progress_update"]
                transferred_bytes = transfer["bytes_received"] - transfer["last_progress_bytes_received"]
                transfer_rate = (transferred_bytes / elapsed_time) / 1024

                logger.info(
                    "[%s] Downloading %s %d%% @ %.2f KB/s / %.2f KB/s",
                    transfer["nick"],
                    transfer["filename"],
                    transfer["percent"],
                    transfer_rate,
                    transfer_rate_avg,
                )
                transfer["last_progress_update"] = now
                transfer["last_progress_bytes_received"] = transfer["bytes_received"]

            # Check MIME type after first chunk
            if transfer["bytes_received"] == 0 and not transfer.get("offset") and self.allowed_mimetypes:
                mime_type = self.mime_checker.from_buffer(data)
                if mime_type not in self.allowed_mimetypes:
                    logger.warning("[%s] Reject %s: Invalid MIME type (%s)", transfer["nick"], transfer["filename"], mime_type)
                    dcc.disconnect()
                    transfer["status"] = "error"
                    transfer["error"] = f"Invalid MIME type ({mime_type})"
                    transfer["connected"] = False
                    dcc.disconnect()
                    try:
                        del self.current_transfers[dcc]
                    except KeyError:
                        pass
                    return

            try:
                with open(transfer["file_path"], "ab") as f:
                    f.write(data)
            except Exception as e:
                logger.error("Error writing to file %s: %s", transfer["file_path"], e)
                transfer["status"] = "error"
                transfer["error"] = f"Error writing to file {transfer['file_path']}: {e}"
                transfer["connected"] = False
                dcc.disconnect()
                try:
                    del self.current_transfers[dcc]
                except KeyError:
                    pass

        transfer["bytes_received"] += len(data)
        # Send 64bit ACK
        if transfer["size"] >= 1024 * 1024 * 1024 * 4:
            dcc.send_bytes(struct.pack("!Q", transfer["bytes_received"] + transfer["offset"]))
        else:
            dcc.send_bytes(struct.pack("!I", transfer["bytes_received"] + transfer["offset"]))

    async def _add_md5_check_queue_item(self, transfer: dict) -> None:
        """Add a transfer to the MD5 check queue.

        This method adds a transfer to the MD5 check queue.
        The file will be processed by the MD5 processor thread in the bot manager.

        Args:
            transfer (dict): The transfer to add to the MD5 check queue.

        """
        await self.bot_manager.md5_check_queue.put(transfer)

    def on_dcc_disconnect(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Handle DCC DISCONNECT messages.

        This method handles the DCC DISCONNECT message, which is sent by the server to the bot when the bot
        should close the DCC connection.

        Args:
            connection (irc.client_aio.AioConnection): The DCC connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        logger.debug("DCC connection lost: %s", event)
        dcc = connection
        if dcc not in self.current_transfers:
            logger.debug("Received DCC disconnect from unknown connection")
            return

        transfer = self.current_transfers[dcc]
        transfer["connected"] = False

        # update timeout
        if transfer["nick"].lower() in self.bot_channel_map:
            for channel in self.bot_channel_map[transfer["nick"].lower()]:
                self.joined_channels[channel] = time.time()

        file_path = transfer["file_path"]
        elapsed_time = time.time() - transfer["start_time"]
        transfer_rate = (transfer["bytes_received"] / elapsed_time) / 1024  # KB/s

        if not os.path.exists(file_path):
            logger.error("[%s] Download failed: %s does not exist", transfer["nick"], file_path)
            if transfer["status"] != "error":
                transfer["status"] = "error"
                transfer["error"] = f"[%s] Download failed: %s does not exist" % (transfer["nick"], file_path)
        else:
            file_size = os.path.getsize(file_path)
            if file_size != transfer["size"]:
                logger.error("[%s] Download %s failed: size mismatch %d != %d", transfer["nick"], transfer["filename"], file_size, transfer["size"])
                if transfer["status"] != "error":
                    transfer["status"] = "failed"
                    transfer["error"] = f"size mismatch {file_size} != {transfer['size']}"
            else:
                logger.info("[%s] Download %s complete - size: %d bytes, %.2f KB/s", transfer["nick"], transfer["filename"], file_size, transfer_rate)
                transfer["completed"] = time.time()
                transfer["status"] = "completed"
                if transfer.get("md5"):
                    loop = asyncio.get_event_loop()
                    loop.run_until_complete(self._add_md5_check_queue_item(transfer))

                if self.config.get("incomplete_suffix") and file_path.endswith(self.config["incomplete_suffix"]):
                    target = file_path[: -len(self.config["incomplete_suffix"])]
                    try:
                        os.rename(file_path, target)
                        logger.info("Renamed downloaded file to %s", transfer["filename"])
                        transfer["file_path"] = target
                    except Exception as e:
                        logger.error("Error renaming %s to %s: %s", file_path, target, e)

        del self.current_transfers[dcc]

    def on_privnotice(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Handle NOTICE messages.

        Redirects NOTICE messages to PRIVMSG.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        return self.on_privmsg(connection, event)

    def on_privmsg(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """Handle PRIVMSG messages.

        This method handles the PRIVMSG message, which is sent by the server to the bot when it
        receives a private message from another user.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.

        """
        self.last_active = time.time()
        sender = event.source.nick
        message = event.arguments[0]
        f = re.search(r"^\*\* Transfer Completed.+ md5sum: ([a-f0-9]{32})", message)
        if f:
            md5sum = f.group(1)
            now = time.time()
            for filename, transfers in self.bot_manager.transfers.items():
                for transfer in transfers:
                    if (
                        transfer["nick"] == sender
                        and transfer["server"] == self.server
                        and transfer.get("completed")
                        and transfer.get("completed", 0) >= now - 30
                        and not transfer.get("md5")
                    ):
                        transfer["md5"] = md5sum
                        logger.info("[%s] MD5 checksum: %s", filename, md5sum)
                        self.bot_manager.md5_check_queue.put_nowait(transfer)
                        break

        #  ** Sending you pack #1 ("TEST.mkv") [1.0GB, MD5:82ce0f4fe6e5c862d54dae475b8a1b82] - (resume+ssl supported)
        f = re.search(r"""^\*\* Sending you pack \#(\d) \("([^"]+)"\).+, MD5:([a-f0-9]{32})""", message, re.I)
        if f:
            filename = f.group(2)
            now = time.time()

            if not filename in self.bot_manager.transfers:
                self.bot_manager.transfers[filename] = []

            self.bot_manager.transfers[filename].append({"nick": sender, "server": self.server, "start_time": now, "completed": False, "md5": f.group(3)})

        f = re.search(r"""^XDCC SEND denied, (.+)""", message, re.I)
        if f:
            error = f.group(1)
            logger.error("[%s] XDCC SEND denied: %s", sender, error)

        logger.info("[%s] %s", sender, message)

    async def cleanup(self, channel_idle_timeout: int, resume_timeout: int) -> None:
        # Find idle channels
        """Clean up idle channels and resume queue.

        This method checks for idle channels and resume queue and cleans them up.
        It will part idle channels and remove old resume requests.

        Args:
            channel_idle_timeout (int): The timeout for idle channels.
            resume_timeout (int): The timeout for resume queue.

        """
        now = time.time()

        if channel_idle_timeout:
            idle_channels = []
            for channel, last_active in self.joined_channels.items():
                if now - last_active > channel_idle_timeout:
                    idle_channels.append(channel)

            # Part idle channels
            for channel in idle_channels:
                await self.part_channel(channel, "Idle timeout")

        for nick, resume_queue in self.resume_queue.items():
            for resume_item in list(resume_queue):
                requested_time = resume_item[-1]
                if now - requested_time > resume_timeout:
                    resume_queue.remove(resume_item)
