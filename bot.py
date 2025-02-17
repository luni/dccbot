import asyncio
import ipaddress
import json
import time
import os
import logging
import random
import string
import shlex
import struct
import re
from aiohttp import web
from irc.connection import AioFactory
from irc.client_aio import AioSimpleIRCClient, AioConnection
import irc.client
import magic
from aiodcc import AioReactor, AioDCCConnection
from typing import Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]%(message)s")
logger = logging.getLogger(__name__)


class IRCBot(AioSimpleIRCClient):
    reactor_class = AioReactor

    def __init__(self,
                 server: str,
                 server_config: dict,
                 download_path: str,
                 allowed_mimetypes: list,
                 max_file_size: int):
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
        self.dcc_transfers = {}  # track active DCC connections
        self.banned_channels = set()
        self.resume_queue = {}
        self.command_queue = asyncio.Queue()
        self.mime_checker = magic.Magic(mime=True)
        self.loop = asyncio.get_event_loop()  # Ensure the loop is set
        self.last_active = time.time()

    @staticmethod
    def get_version():
        """Returns the bot version.

        Used when answering a CTCP VERSION request.
        """
        return "dccbot 1.0"

    def _generate_random_nick(self, base_nick: str) -> str:
        """Generate a random IRC nick by appending a 3-digit random number to the given base nick.

        Args:
            base_nick (str): The base nick to use for generating the full nick.

        Returns:
            str: The full nick with a random 3-digit suffix.
        """
        random_suffix = ''.join(random.choices(string.digits, k=3))
        return f"{base_nick}{random_suffix}"

    async def connect(self):
        """
        Establish a connection to the IRC server.

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
                connect_factory = AioFactory(ssl=True)
                port = self.server_config.get("port", 6697)
            else:
                connect_factory = AioFactory()
                port = self.server_config.get("port", 6667)

            await self.connection.connect(self.server, port, self.nick, connect_factory=connect_factory)
            logger.info(f"Connecting to server: {self.server} with nick: {self.nick}")
        except Exception as e:
            logger.error(f"Connection error to {self.server}: {e}")

    async def disconnect(self, reason: Optional[str] = None):
        """
        Disconnect the bot from the IRC server.

        Args:
            reason (Optional[str]): Optional quit message to send to the server.
        """
        self.connection.disconnect(reason)
        logger.info(f"Disconnected from server {self.server} ({reason})")

    async def join_channel(self, channel: str):
        """
        Join the specified channel.

        Args:
            channel (str): The channel to join.

        If the channel is empty or the bot is already in the channel,
        this function does nothing and returns.
        """

        if not channel or channel in self.joined_channels:
            return

        self.connection.join(channel)
        logger.info(f"Try to join channel: {channel}")

    async def part_channel(self, channel: str, reason: Optional[str] = None):
        """
        Part the specified channel.

        Args:
            channel (str): The channel to part.
            reason (Optional[str]): Optional part message to send to the server.
        """
        if channel not in self.joined_channels:
            # If the channel is empty or the bot is not in the channel, do nothing
            return

        self.connection.part(channel)
        logger.info(f"Parted channel: {channel} ({reason})")
        self.last_active = time.time()
        del self.joined_channels[channel.lower()]

    async def queue_command(self, data: dict):
        """
        Queue a command to be processed by the bot.

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
        logger.debug(f"Queued command: {data}")

    async def process_command_queue(self):
        """
        Process commands from the command queue.

        This function runs an infinite loop that checks the command queue for new commands. If a command is found,
        it will be processed according to the command. The commands can be one of the following:

        - send: Send a message to the specified user/channel.
        - join: Join the specified channel.
        - part: Part the specified channel.

        Args:
            None

        Returns:
            None
        """
        while True:
            data = await self.command_queue.get()
            self.last_active = time.time()
            if not data:
                continue

            if data['command'] in ('send', 'join'):
                if data.get('channels'):
                    for channel in data['channels']:
                        await self.join_channel(channel)

                if not data.get('user') or not data.get('message'):
                    continue

                # wait until bot joined channel
                if data.get('channels'):
                    waiting_channels = data['channels']
                    retry = 0
                    while retry < 10 and waiting_channels:
                        for channel in list(waiting_channels):
                            if channel.lower() in self.joined_channels:
                                waiting_channels.remove(channel)

                        await asyncio.sleep(1)
                        retry += 1

                    if waiting_channels:
                        logger.warning(f"Failed to join channels {', '.join(waiting_channels)} after 10 seconds")
                        continue

                try:
                    self.connection.privmsg(data.get('user'), data.get('message'))
                    logger.info(f"Sent message to {data.get('user')}: {data.get('message')}")
                except Exception as e:
                    logger.error(f"Failed to send message to {data.get('user')}: {e}")
            elif data['command'] == 'part':
                if data.get('channels'):
                    for channel in data['channels']:
                        await self.part_channel(channel)

    def on_welcome(self, connection: AioConnection, event: irc.client_aio.Event):
        """
        Called when the bot receives the welcome message from the server.

        If the bot is configured to authenticate with NickServ, this method sends the
        IDENTIFY command to NickServ.

        Also joins channels, of the bot is configured to join channels.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.
        """
        logger.info(f"Connected to server: {self.server}")

        # Authenticate with NickServ
        if self.server_config.get("nickserv_password"):
            self.connection.privmsg("NickServ", f"IDENTIFY {self.server_config['nickserv_password']}")
            logger.info("Sent NickServ IDENTIFY command")

        # Join channels
        for channel in self.server_config.get("channels", []):
            asyncio.create_task(self.join_channel(channel))

        # Start processing the message queue
        asyncio.create_task(self.process_command_queue())

    def on_motd(self, connection: AioConnection, event: irc.client_aio.Event):
        """
        Called when the bot receives the MOTD (Message of the Day) message from the server.

        The MOTD message is sent by the server to the bot when the bot connects to the server. The message
        contains information about the server and its configuration.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.
        """
        # logger.info(event.arguments[0])
        pass

    def on_nosuchnick(self, connection: AioConnection, event: irc.client_aio.Event):
        """Called when the bot receives a NO SUCH NICK message from the server."""
        logger.info("Failed to send message: %s", event.arguments[0])

    def on_bannedfromchan(self, connection: AioConnection, event: irc.client_aio.Event):
        """Called when the bot receives a BANNEDFROMCHAN message from the server."""
        logger.info("Banned from channel %s: %s", event.target, event.arguments[0])
        channel_name = event.arguments[0].lower()
        self.banned_channels.add(channel_name)
        if channel_name in self.joined_channels:
            del self.joined_channels[channel_name]

    def on_part(self, connection: AioConnection, event: irc.client_aio.Event):
        """Called when the bot receives a PART message from the server."""
        if event.source.nick != self.nick:
            return

        channel_name = event.target.lower()
        if channel_name in self.joined_channels:
            logger.info("Left channel %s: %s", event.target, event.arguments)
            del self.joined_channels[channel_name]

    def on_join(self, connection: AioConnection, event: irc.client_aio.Event):
        """Called when the bot joins a channel."""
        if event.source.nick != self.nick:
            return

        channel_name = event.target.lower()
        if channel_name not in self.joined_channels:
            logger.info("Joined channel %s: %s", event.target, event.arguments)
            self.joined_channels[channel_name] = time.time()
            self.banned_channels.discard(channel_name)

    def on_kick(self, connection: AioConnection, event: irc.client_aio.Event):
        """Called when the bot is kicked from a channel."""
        logger.info("Kicked from channel %s: %s", event.target, event.arguments)
        channel_name = event.target.lower()
        if channel_name in self.joined_channels:
            del self.joined_channels[channel_name]

    def on_ctcp(self, connection: AioConnection, event: irc.client_aio.Event):
        """
        Called when the bot receives a CTCP message from the server.

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

        if event.arguments[0] != "DCC":
            logger.info("CTCP: %s", event)
            return

        if not event.arguments or len(event.arguments) < 2:
            logger.warning("Invalid DCC event: %s", event)
            return

        if event.arguments[1].startswith("ACCEPT "):
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

                if peer_port < 1 or peer_port > 65535:
                    logger.warning("Invalid DCC SEND command (invalid port): %s", event.arguments)
                    return

                if resume_position < 1:
                    logger.warning("Invalid DCC SEND command (invalid resume_position): %s", event.arguments)
                    return
            except ValueError:
                logger.warning("Invalid DCC SEND command (invalid size or port): %s", event.arguments)
                return

            for item in self.resume_queue[event.source.nick]:
                logging.info("item: %s", item)
                if peer_port != item[1] or resume_position != item[3]:
                    continue

                self.resume_queue[event.source.nick].remove(item)
                break
            else:
                logger.warning("DCC ACCEPT command for unknown file: %s", event)
                return

            if not self.resume_queue[event.source.nick]:
                del self.resume_queue[event.source.nick]

            self.init_dcc_connection(event.source.nick, item[0], peer_port, item[2], item[4], resume_position, False)

        if event.arguments[1].startswith("SEND ") or event.arguments[1].startswith("SSEND "):
            use_ssl = False
            if event.arguments[1].startswith("SSEND "):
                use_ssl = True

            payload = event.arguments[1]
            parts = shlex.split(payload)
            if len(parts) != 5:
                logger.warning("Invalid DCC SEND command (not enough arguments)")
                return

            file_name, peer_address, peer_port, size = parts[1:]

            # handle v6
            if ':' in peer_address:
                # Validate the IP address
                try:
                    ipaddress.ip_address(peer_address)
                except ValueError:
                    logger.warning(f"Rejected {file_name}: Invalid IP address {peer_address}")
                    return
            else:
                try:
                    # Convert the IP address to a quad-dotted form
                    peer_address = irc.client.ip_numstr_to_quad(peer_address)
                except ValueError:
                    logger.warning(f"Rejected {file_name}: Invalid IP address {peer_address}")
                    return

            def is_valid_filename(path: str, filename: str) -> bool:
                if not filename:
                    return False

                file_path = os.path.join(path, filename)

                if not os.path.isabs(file_path):
                    return False

                if '/' in filename or '\\' in filename:
                    return False

                # Optionally: Check for platform-specific invalid characters
                # This is optional and depends on your target platform
                invalid_chars = set('/\\:*?"<>|')  # Invalid on Windows
                if any(char in invalid_chars for char in filename):
                    return False

                return True

            # validate file name
            if not is_valid_filename(self.download_path, file_name):
                logger.warning("Invalid DCC SEND command (file name contains invalid characters): %s", file_name)
                return

            try:
                size = int(size)
                peer_port = int(peer_port)

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
                logger.warning(f"Rejected {file_name}: File size exceeds limit ({size} > {self.max_file_size})")
                return

            download_path = os.path.join(self.download_path, file_name)
            completed = False
            if os.path.exists(download_path):
                local_size = os.path.getsize(download_path)
                if local_size == size:
                    logger.warning(f"Rejected {file_name}: File already complete")
                    completed = True
                elif local_size > size:
                    logger.warning(f"Rejected {file_name}: Local file larger then remote file ({local_size} > {size})")
                    completed = True
                else:
                    logger.info(f"Send DCC RESUME {file_name} starting at {local_size} bytes")
                    self.connection.ctcp_reply(event.source.nick, ' '.join(["DCC", "RESUME", '"' + file_name.replace('"', '') + '"', str(peer_port), str(local_size)]))
                    if event.source.nick not in self.resume_queue:
                        self.resume_queue[event.source.nick] = set()

                    self.resume_queue[event.source.nick].add((peer_address, peer_port, file_name, local_size, size, use_ssl))
                    return

            self.init_dcc_connection(event.source.nick, peer_address, peer_port, file_name, size, 0, completed, use_ssl)

    def init_dcc_connection(self,
                            peer_name: str,
                            peer_address: str,
                            peer_port: int,
                            file_name: str,
                            size: int,
                            offset: Optional[int] = None,
                            completed: Optional[bool] = None,
                            use_ssl: Optional[bool] = False):
        """
        Initialize a DCC connection to a peer.

        This method sets up a DCC connection to the peer, creates the
        file to receive the data and stores the information in the
        `dcc_transfers` dictionary.

        Args:
            peer_name (str): The name of the peer.
            peer_address (str): The address of the peer.
            peer_port (int): The port of the peer.
            file_name (str): The name of the file to receive.
            size (int): The size of the file.
            offset (int): The offset of the file to resume from.
            completed (bool): Whether the file transfer is completed.
            use_ssl (bool): Whether to use SSL.
        """
        dcc_msg = "Receiving file via DCC" if not use_ssl else "Receiving file via SSL DCC"
        logger.info(f"{dcc_msg} {file_name} from {peer_address}:{peer_port}, size: {size} bytes")

        # Convert the port to an integer
        logger.info("Connecting to %s:%s", peer_address, peer_port)

        # Create a new DCC connection
        dcc: AioDCCConnection = self.dcc('raw')

        connect_factory = None
        if use_ssl:
            connect_factory = AioFactory(ssl=True)
        else:
            connect_factory = AioFactory()

        # Schedule the connection to be established
        try:
            self.loop.create_task(dcc.connect(peer_address, peer_port, connect_factory=connect_factory))
        except Exception as e:
            logger.error(f"Failed to connect to {peer_address}:{peer_port}: {e}")
            return

        # Store the information about the file transfer
        self.dcc_transfers[dcc] = {
            "peer_name": peer_name,
            "peer_address": peer_address,
            "peer_port": peer_port,
            "file_path": os.path.join(self.download_path, file_name),
            "file_name": file_name,
            "start_time": time.time(),
            "bytes_received": 0,
            "offset": offset,
            "size": size,
            "ssl": use_ssl,
            "percent": 0,
            "completed": completed,
            "last_progress_update": 0,
            "last_progress_bytes_received": 0
        }

    def on_dccmsg(self, connection: AioConnection, event: irc.client_aio.Event) -> None:
        """
        Called when the bot receives a DCC message from the server.

        This method handles the DCC message, which is sent by the server to the bot when the bot
        should receive a DCC file transfer. The message contains the chunk of data from the file.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.
        """
        dcc = connection
        if dcc not in self.dcc_transfers:
            logger.warning("Received DCC message from unknown connection")
            return
        transfer = self.dcc_transfers[dcc]

        if transfer["completed"]:
            dcc.disconnect()
            return

        now = time.time()
        percent = int(100 * (transfer['bytes_received'] + transfer['offset']) / transfer['size'])
        if transfer["percent"] + 10 <= percent or now - transfer["last_progress_update"] >= 5:
            transfer["percent"] = percent
            elapsed_time = now - transfer["start_time"]
            transfer_rate_avg = (transfer["bytes_received"] / elapsed_time) / 1024 if elapsed_time > 0 else 0

            elapsed_time = now - transfer["last_progress_update"]
            transfered_bytes = transfer["bytes_received"] - transfer["last_progress_bytes_received"]
            transfer_rate = (transfered_bytes / elapsed_time) / 1024

            logger.info(f"Downloading {transfer['file_name']} {transfer['percent']}% @ {transfer_rate:.2f} KB/s / {transfer_rate_avg:.2f} KB/s")
            transfer["last_progress_update"] = now
            transfer["last_progress_bytes_received"] = transfer["bytes_received"]

        file_path = transfer["file_path"]
        data = event.arguments[0]

        # Check MIME type after first chunk
        if transfer["bytes_received"] == 0 and not transfer.get('offset'):
            mime_type = self.mime_checker.from_buffer(data)
            if mime_type not in self.allowed_mimetypes:
                logger.warning(f"Rejected {file_path}: Invalid MIME type ({mime_type})")
                del self.dcc_transfers[dcc]
                dcc.disconnect()
                return

        transfer["bytes_received"] += len(data)

        try:
            with open(file_path, "ab") as f:
                f.write(data)
        except Exception as e:
            logger.error(f"Error writing to file {file_path}: {e}")
            del self.dcc_transfers[dcc]
            dcc.disconnect()

        dcc.send_bytes(struct.pack("!I", transfer["bytes_received"]))

    def on_dcc_disconnect(self, connection: AioConnection, event: irc.client_aio.Event):
        """
        Called when the bot receives a DCC DISCONNECT message from the server.

        This method handles the DCC DISCONNECT message, which is sent by the server to the bot when the bot
        should close the DCC connection.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.
        """
        dcc = connection
        if dcc not in self.dcc_transfers:
            logger.warning("Received DCC disconnect from unknown connection")
            return

        transfer = self.dcc_transfers[dcc]
        file_path = transfer["file_path"]
        elapsed_time = time.time() - transfer["start_time"]
        transfer_rate = (transfer["bytes_received"] / elapsed_time) / 1024  # KB/s

        if not os.path.exists(file_path):
            logger.error(f"Download failed: {file_path} does not exist")
        else:
            file_size = os.path.getsize(file_path)
            if file_size != transfer["size"]:
                logger.error("%s Download failed: size mismatch %s != %s", transfer["file_name"], file_size, transfer["size"])
            else:
                logger.info(
                    f"Download complete: {transfer['file_name']}, size: {file_size} bytes, "
                    f"transfer rate: {transfer_rate:.2f} KB/s"
                )
        del self.dcc_transfers[dcc]

    def on_privnotice(self, connection: AioConnection, event: irc.client_aio.Event):
        return self.on_privmsg(connection, event)

    def on_privmsg(self, connection: AioConnection, event: irc.client_aio.Event):
        """
        Called when the bot receives a PRIVMSG message from the IRC server.

        This method handles the PRIVMSG message, which is sent by the server to the bot when it
        receives a private message from another user.

        Args:
            connection (irc.client_aio.AioConnection): The connection to the IRC server.
            event (irc.client_aio.Event): The event that triggered this method to be called.
        """
        self.last_active = time.time()
        sender = event.source.nick
        message = event.arguments[0]
        logger.info(f"[{sender}] {message}")


class IRCBotManager:
    def __init__(self, config_file):
        self.config_file = config_file
        self.config = self.load_config()
        self.bots = {}
        self.server_idle_timeout = self.config.get("server_idle_timeout", 1800)  # 30 minutes
        self.channel_idle_timeout = self.config.get("channel_idle_timeout", 1800)  # 30 minutes

    def load_config(self) -> dict:
        """
        Load the configuration from a JSON file.

        Returns the configuration as a dictionary.
        Raises a ValueError if the configuration is invalid.
        """
        try:
            with open(self.config_file, "r") as f:
                config = json.load(f)
            if "servers" not in config:
                raise ValueError("Missing 'servers' key in config")
            return config
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            raise

    async def get_bot(self, server: str) -> IRCBot:
        """
        Get an IRCBot instance for a server.

        If the server is not in the bot manager, a new IRCBot instance will
        be created with the server's configuration.

        Args:
            server: The server to get the IRCBot instance for.

        Returns:
            The IRCBot instance for the server.
        """
        if server not in self.bots:
            server_config = self.config["servers"].get(server, {})
            if not server_config:
                raise ValueError(f"Unknown server: {server}")
            bot = IRCBot(
                server,
                server_config,
                self.config.get("default_download_path", "./downloads"),
                self.config.get("allowed_mimetypes", []),
                self.config.get("max_file_size", 100 * 1024 * 1024),  # Default: 100 MB
            )
            self.bots[server] = bot
            await bot.connect()
        return self.bots[server]

    async def cleanup(self) -> None:
        """
        Clean up idle bots and channels.

        This is a background task that runs indefinitely. It periodically checks
        for idle servers and channels and cleans them up.

        Raises:
            Exception: If an unhandled exception occurs.
        """
        while True:
            try:
                # Get the current time
                now = time.time()

                # Find idle servers and channels
                idle_servers = []
                for server, bot in self.bots.items():
                    # Find idle channels
                    idle_channels = []
                    for channel, last_active in bot.joined_channels.items():
                        if self.channel_idle_timeout > 0 and now - last_active > self.channel_idle_timeout:
                            idle_channels.append(channel)

                    # Part idle channels
                    for channel in idle_channels:
                        await bot.part_channel(channel, "Idle timeout")

                    # Check if the server is idle
                    if (
                        not bot.joined_channels
                        and not bot.dcc_transfers
                        and bot.command_queue.empty()
                        and server_idle_timeout > 0 and bot.last_active + self.server_idle_timeout < now
                    ):
                        idle_servers.append(server)

                # Disconnect idle servers
                for server in idle_servers:
                    await self.bots[server].disconnect("Idle timeout")
                    del self.bots[server]

                # Wait 1 second before checking again
                await asyncio.sleep(1)
            except Exception as e:
                # Log the exception and wait 10 seconds before trying again
                logger.exception(e)
                await asyncio.sleep(10)


async def start_background_tasks(app: web.Application) -> None:
    """
    Start the background task for cleaning up idle bots.

    This function is intended to be added as an on_startup handler for an aiohttp web application.

    Args:
        app: The aiohttp web application.
    """
    bot_manager = app["bot_manager"]
    app["cleanup_task"] = asyncio.create_task(bot_manager.cleanup())


async def cleanup_background_tasks(app: web.Application) -> None:
    """
    Cancel the background task for cleaning up idle bots.

    This function is intended to be added as an on_cleanup handler for an aiohttp web application.

    Args:
        app: The aiohttp web application.

    Returns:
        None
    """
    # Cancel the background task, which will allow it to exit cleanly
    app["cleanup_task"].cancel()
    # Wait for the task to finish
    await app["cleanup_task"]


async def handle_join(request: web.Request) -> web.Response:
    """
    Handle a request to join a channel.

    The request should contain the following JSON payload:
    {
        "server": str,
        "channel": str
    }

    If the channel is not specified, the request will be rejected with a 400 status code.

    If the server is not connected, this function will start a new connection.

    If the bot is already in the channel, this function will not do anything.

    Returns a JSON response with the following format:
    {
        "status": str
    }
    If the status is "ok", the request was successful. If the status is "error", the request was rejected and the response will contain an error message.
    """
    try:
        data: dict = await request.json()
        bot: IRCBot = await request.app["bot_manager"].get_bot(data["server"])
        if not data.get('channel'):
            return web.json_response({"status": "error", "message": "Missing channel"}, status=400)

        asyncio.create_task(bot.queue_command({
            "command": "join",
            "channels": data.get('channels', [data.get('channel', '')])
        }))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_part(request: web.Request) -> web.Response:
    """
    Handle a request to part a channel.

    The request should contain the following JSON payload:
    {
        "server": str,
        "channel": str
    }

    If the channel is not specified, the request will be rejected with a 400 status code.

    If the server is not connected, this function will start a new connection.

    If the bot is already in the channel, this function will not do anything.

    Returns a JSON response with the following format:
    {
        "status": str
    }
    If the status is "ok", the request was successful. If the status is "error", the request was rejected and the response will contain an error message.
    """
    try:
        data: dict = await request.json()
        bot: IRCBot = await request.app["bot_manager"].get_bot(data["server"])
        if not data.get('channel'):
            return web.json_response({"status": "error", "message": "Missing channel"}, status=400)

        asyncio.create_task(bot.queue_command({
            "command": "part",
            "channels": data.get('channels', [data.get('channel', '')])
        }))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_msg(request: web.Request) -> web.Response:
    """
    Handle a request to send a message to a user.

    The request should contain the following JSON payload:
    {
        "server": str,
        "channel": str,
        "user": str,
        "message": str
    }

    If the channel is not specified, the request will be rejected with a 400 status code.

    If the server is not connected, this function will start a new connection.

    If the bot is already in the channel, this function will not do anything.

    Returns a JSON response with the following format:
    {
        "status": str
    }
    If the status is "ok", the request was successful. If the status is "error", the request was rejected and the response will contain an error message.
    """
    try:
        data: dict = await request.json()
        if not data.get('user') or not data.get('message'):
            return web.json_response({"status": "error", "message": "Missing user or message"}, status=400)
        bot: IRCBot = await request.app["bot_manager"].get_bot(data["server"])
        asyncio.create_task(bot.queue_command({
            "command": "send",
            "channels": data.get("channels", [data.get('channel', '')]),
            "user": data["user"],
            "message": data["message"]
        }))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_shutdown(request: web.Request) -> web.Response:
    """
    Handle a request to shut down the server.

    The request should not contain any payload.

    This function will shut down the server and disconnect all IRC connections.

    Returns a JSON response with the following format:
    {
        "status": str
    }
    If the status is "ok", the request was successful. If the status is "error", the request was rejected and the response will contain an error message.
    """
    logger.info("Shutting down server...")
    for bot in request.app["bot_manager"].bots.values():
        await bot.disconnect("Shutting down")
    await request.app.shutdown()
    return web.json_response({"status": "ok"})


async def handle_info(request: web.Request):
    """
    Handle a request to get information about all connections.

    Returns a JSON response with the following format:
    {
        "networks": [
            {
                "server": str,
                "connected": bool,
                "nickname": str,
                "channels": [
                    {
                        "name": str,
                        "last_active": float
                    }
                ]
            }
        ],
        "dcc_transfers": [
            {
                "server": str,
                "filename": str,
                "peer": str,
                "size": int,
                "received": int,
                "speed": float,
                "status": str
            }
        ]
    }
    """
    try:
        bot_manager = request.app["bot_manager"]
        response = {
            "networks": [],
            "transfers": []
        }

        # Gather information about all networks and channels
        for server, bot in bot_manager.bots.items():
            network_info = {
                "server": server,
                "nickname": bot.nick,
                "channels": []
            }

            # Add channel information
            for channel, last_active in bot.joined_channels.items():
                network_info["channels"].append({
                    "name": channel,
                    "last_active": last_active
                })

            response["networks"].append(network_info)

            # Add DCC transfer information
            for dcc_connection, transfer in bot.dcc_transfers.items():
                now = time.time()

                transfered_bytes = transfer['bytes_received']
                transfer_time = now - transfer['start_time'] if transfer['start_time'] else 0
                speed_avg = transfered_bytes / transfer_time / 1024 if transfer_time > 0 else 0

                transfered_bytes = transfer["bytes_received"] - transfer["last_progress_bytes_received"]
                transfer_time = now - transfer["last_progress_update"]
                speed = (transfered_bytes / transfer_time) / 1024

                transfer_info = {
                    "server": server,
                    "filename": transfer['file_name'],
                    "nick": transfer['peer_name'],
                    "host": transfer['peer_address'] + ":" + str(transfer['peer_port']),
                    "size": transfer['size'],
                    "received": transfer["bytes_received"],
                    "speed": round(speed, 2),  # bytes per second
                    "speed_avg": round(speed_avg, 2)  # bytes per second
                }
                response["transfers"].append(transfer_info)

        return web.json_response(response)
    except Exception as e:
        logging.exception(e)
        logger.error(f"Error in handle_info: {str(e)}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


app = web.Application()
app["bot_manager"] = IRCBotManager("config.json")
app.on_startup.append(start_background_tasks)
app.on_cleanup.append(cleanup_background_tasks)

app.router.add_post("/join", handle_join)
app.router.add_post("/part", handle_part)
app.router.add_get("/info", handle_info)
app.router.add_post("/msg", handle_msg)
app.router.add_post("/shutdown", handle_shutdown)

web.run_app(app, port=8080)
