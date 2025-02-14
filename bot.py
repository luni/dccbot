import asyncio
import json
import time
import os
import logging
import ssl
import random
import string
import shlex
import struct
from aiohttp import web
from irc.client_aio import AioSimpleIRCClient, AioConnection, AioReactor
import irc.client
import magic

# Configure logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)



class IRCBot(AioSimpleIRCClient):
    reactor_class = AioReactor

    def __init__(self, server, nick, nickserv_password, download_path, allowed_mimetypes, max_file_size, use_tls, random_nick):
        super().__init__()
        self.server = server
        self.nick = self._generate_random_nick(nick) if random_nick else nick
        self.nickserv_password = nickserv_password
        self.download_path = download_path
        self.allowed_mimetypes = allowed_mimetypes
        self.max_file_size = max_file_size
        self.use_tls = use_tls
        self.joined_channels = {}  # (channel) -> last active time
        self.dcc_transfers = {}  # track active DCC connections
        self.command_queue = asyncio.Queue()
        self.mime_checker = magic.Magic(mime=True)
        self.loop = asyncio.get_event_loop()  # Ensure the loop is set

    def _handle_event(self, connection, event):
        logging.info(event)
        pass

    @staticmethod
    def get_version():
        """Returns the bot version.

        Used when answering a CTCP VERSION request.
        """
        return "dccbot 1.0"

    def on_welcome(self, connection, event):
        """Called when the bot receives the welcome message from the server."""
        logger.info(f"Connected3 to server: {self.server}")

        # Authenticate with NickServ
        if self.nickserv_password:
            self.connection.privmsg("NickServ", f"IDENTIFY {self.nickserv_password}")
            logger.info("Sent NickServ IDENTIFY command")

        # Start processing the message queue
        asyncio.create_task(self.process_command_queue())

    def _generate_random_nick(self, base_nick):
        random_suffix = ''.join(random.choices(string.digits, k=3))
        return f"{base_nick}_{random_suffix}"

    async def connect(self):
        try:
            if self.use_tls:
                # Create SSL context
                ssl_context = ssl.create_default_context()
                # Define a custom connect_factory for TLS

                def tls_connect_factory():
                    return asyncio.open_connection(
                        self.server, 6697, ssl=ssl_context
                    )
                # Initialize AioConnection with the custom connect_factory
                self.connection = AioConnection(self.reactor, connect_factory=tls_connect_factory)
            else:
                # Initialize AioConnection without TLS
                self.connection = AioConnection(self.reactor)

            # Connect to the IRC server
            await self.connection.connect(self.server, 6697 if self.use_tls else 6667, self.nick)
            logger.info(f"Connecting to server: {self.server} with nick: {self.nick}")
        except Exception as e:
            logger.error(f"Connection error to {self.server}: {e}")

    def _on_connect(self, connection, event):
        """Called when the bot successfully connects to the server."""
        logger.info(f"Connected2 to server: {self.server}")
        # Perform any post-connection setup here

    def on_motd(self, connection, event):
        """Called when the bot receives the MOTD message from the server."""
        logger.info(event.arguments[0])

    def on_nosuchnick(self, connection, event):
        """Called when the bot receives a NO SUCH NICK message from the server."""
        logger.info("Failed to send message: " + event.arguments[0])

    async def join_channel(self, channel):
        if not channel or channel in self.joined_channels:
            return

        self.connection.join(channel)
        self.joined_channels[channel] = time.time()
        logger.info(f"Joined channel: {channel}")

    async def part_channel(self, channel):
        if channel in self.joined_channels:
            del self.joined_channels[channel]
            self.connection.part(channel)
            logger.info(f"Parted channel: {channel}")

    async def queue_command(self, data: dict):
        await self.command_queue.put(data)
        logger.debug(f"Queued command: {data}")

    async def process_command_queue(self):
        while True:
            data = await self.command_queue.get()
            if not data:
                continue

            if data['command'] in ('send', 'join'):
                if data.get('channels'):
                    for channel in data['channels']:
                        await self.join_channel(channel)

                if not data.get('user') or not data.get('message'):
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

    def on_ctcp(self, connection, event):
        logging.info("CTCP: %s", event)
        # 'DCC', 'SEND [DsunS]Madan_no_Ou_to_Vanadis_01_[10Bit_H264_720p_AAC][52B760A2].mkv 3118240803 4000 441339717'], tags: []
        if not event.arguments or len(event.arguments) < 2:
            return

        if event.arguments[0] == "DCC" and event.arguments[1].startswith("SEND "):

            payload = event.arguments[1]
            parts = shlex.split(payload)
            if len(parts) != 5:
                logger.warning("Invalid DCC SEND command")
                return

            file_name, peer_address, peer_port, size = parts[1:]

            size = int(size)

            if size > self.max_file_size:
                logger.warning(f"Rejected {file_name}: File size exceeds limit ({size} > {self.max_file_size})")
                return

            download_path = os.path.join(self.download_path, file_name)
            logger.info(f"Receiving DCC file {file_name} from {peer_address}:{peer_port}, size: {size} bytes")

            resume = False
            completed = False
            if os.path.exists(download_path):
                resume = os.path.getsize(download_path)
                if resume == int(size):
                    logger.warning(f"Rejected {file_name}: File already complete")
                    completed = True
                else:
                    logger.info(f"Resuming DCC file {file_name} from {resume} bytes")
                    self.connection.ctcp_reply(event.source.nick, shlex.join(["DCC", "RESUME", file_name, str(peer_port), str(resume)]))


            peer_address = irc.client.ip_numstr_to_quad(peer_address)
            peer_port = int(peer_port)
            dcc = self.dcc()
            dcc.connect(peer_address, peer_port)
            if completed:
                dcc.disconnect()
                return

            if not dcc.connected:
                logger.warning(f"Failed to connect to {peer_address}:{peer_port}")
                return

            self.dcc_transfers[dcc] = {
                "file_path": download_path,
                "start_time": time.time(),
                "bytes_received": 0,
                "size": size,
                "resume": resume
            }

    def on_dccmsg(self, connection, event):
        logging.info("DCC: %s", event)
        dcc = connection
        if dcc not in self.dcc_transfers:
            logger.warning("Received DCC message from unknown connection")
            return
        transfer = self.dcc_transfers[dcc]

        file_path = transfer["file_path"]
        data = event.arguments[0]
        transfer["bytes_received"] += len(data)

        # Check MIME type after first chunk
        if transfer["bytes_received"] == len(data):
            mime_type = self.mime_checker.from_buffer(data)
            if mime_type not in self.allowed_mimetypes:
                logger.warning(f"Rejected {file_path}: Invalid MIME type ({mime_type})")
                del self.dcc_transfers[dcc]
                dcc.disconnect()
                return

        try:
            with open(file_path, "ab") as f:
                f.write(data)
        except Exception as e:
            logger.error(f"Error writing to file {file_path}: {e}")
            del self.dcc_transfers[dcc]
            dcc.disconnect()

        dcc.send_bytes(struct.pack("!I", transfer["bytes_received"]))

    def on_dcc_disconnect(self, connection, event):
        dcc = connection
        if dcc not in self.dcc_transfers:
            logger.warning("Received DCC disconnect from unknown connection")
            return
        transfer = self.dcc_transfers[dcc]
        file_path = transfer["file_path"]
        elapsed_time = time.time() - transfer["start_time"]
        transfer_rate = (transfer["bytes_received"] / elapsed_time) / 1024  # KB/s

        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            logger.info(
                f"Download complete: {file_path}, size: {file_size} bytes, "
                f"transfer rate: {transfer_rate:.2f} KB/s"
            )
        else:
            logger.error(f"Download failed: {file_path} does not exist")
        del self.dcc_transfers[dcc]

    def on_privmsg(self, connection, event):
        logging.info(event)
        # sender = event.source.nick
        # message = event.arguments[0]
        # logger.info(f"[PRIVATE MSG] {sender}: {message}")


class IRCBotManager:
    def __init__(self, config_file):
        self.config_file = config_file
        self.config = self.load_config()
        self.bots = {}
        self.idle_timeout = self.config.get("idle_timeout", 300)  # Default: 5 minutes
        self.cleanup_interval = self.config.get("cleanup_interval", 60)  # Default: 1 minute

    def load_config(self):
        try:
            with open(self.config_file, "r") as f:
                config = json.load(f)
            if "servers" not in config:
                raise ValueError("Missing 'servers' key in config")
            return config
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            raise

    async def update_server_config(self, server, server_config):
        """Update or add a server configuration."""
        self.config["servers"][server] = server_config
        await self.save_config()
        logger.info(f"Updated server config for {server}")

    async def remove_server(self, server):
        """Remove a server configuration."""
        if server in self.config["servers"]:
            del self.config["servers"][server]
            await self.save_config()
            logger.info(f"Removed server config for {server}")
        else:
            logger.warning(f"Server {server} not found in config")

    async def save_config(self):
        """Save the current config to disk."""
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.config, f, indent=4)
            logger.info("Config saved to disk")
        except Exception as e:
            logger.error(f"Error saving config: {e}")

    async def get_bot(self, server):
        if server not in self.bots:
            server_config = self.config["servers"].get(server, {})
            bot = IRCBot(
                server,
                server_config.get("nick", "BotNick"),
                server_config.get("nickserv_password", ""),
                self.config.get("default_download_path", "./downloads"),
                self.config.get("allowed_mimetypes", []),
                self.config.get("max_file_size", 100 * 1024 * 1024),  # Default: 100 MB
                server_config.get("use_tls", False),
                server_config.get("random_nick", False),
            )
            self.bots[server] = bot
            await bot.connect()
        return self.bots[server]

    async def cleanup(self):
        while True:
            now = time.time()
            for server, bot in list(self.bots.items()):
                if (
                    all(now - last_active > self.idle_timeout for last_active in bot.joined_channels.values())
                    and not bot.dcc_transfers
                    and bot.message_queue.empty()
                ):
                    await bot.disconnect("Idle timeout")
                    del self.bots[server]
                    logger.info(f"Disconnected idle bot for server: {server}")
            await asyncio.sleep(self.cleanup_interval)


async def start_background_tasks(app):
    bot_manager = app["bot_manager"]
    app["cleanup_task"] = asyncio.create_task(bot_manager.cleanup())


async def cleanup_background_tasks(app):
    app["cleanup_task"].cancel()
    await app["cleanup_task"]


async def handle_join(request):
    try:
        data = await request.json()
        bot = await request.app["bot_manager"].get_bot(data["server"])
        if not data.get('channel'):
            return web.json_response({"status": "error", "message": "Missing channel"}, status=400)

        asyncio.create_task(bot.queue_command({
            "command": "join",
            "channels": data.get('channels', [data.get('channel', '')])
        }))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_part(request):
    try:
        data = await request.json()
        bot = await request.app["bot_manager"].get_bot(data["server"])
        if not data.get('channel'):
            return web.json_response({"status": "error", "message": "Missing channel"}, status=400)

        asyncio.create_task(bot.queue_command({
            "command": "part",
            "channels": data.get('channels', [data.get('channel', '')])
        }))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_msg(request):
    try:
        data = await request.json()
        bot = await request.app["bot_manager"].get_bot(data["server"])
        asyncio.create_task(bot.queue_command({
            "command": "send",
            "channels": data.get("channels", [data.get('channel', '')]),
            "user": data["user"],
            "message": data["message"]
        }))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_shutdown(request):
    logger.info("Shutting down server...")
    for bot in request.app["bot_manager"].bots.values():
        await bot.disconnect("Shutting down")
    await request.app.shutdown()
    return web.json_response({"status": "ok"})


async def handle_update_server(request):
    try:
        data = await request.json()
        server = data.get("server")
        server_config = data.get("config")
        if not server or not server_config:
            return web.json_response({"status": "error", "message": "Missing server or config"}, status=400)

        await request.app["bot_manager"].update_server_config(server, server_config)
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


async def handle_remove_server(request):
    try:
        data = await request.json()
        server = data.get("server")
        if not server:
            return web.json_response({"status": "error", "message": "Missing server"}, status=400)

        await request.app["bot_manager"].remove_server(server)
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)

app = web.Application()
app["bot_manager"] = IRCBotManager("config.json")
app.on_startup.append(start_background_tasks)
app.on_cleanup.append(cleanup_background_tasks)

app.router.add_post("/join", handle_join)
app.router.add_post("/part", handle_part)
app.router.add_post("/msg", handle_msg)
app.router.add_post("/shutdown", handle_shutdown)
app.router.add_post("/update_server", handle_update_server)
app.router.add_post("/remove_server", handle_remove_server)

web.run_app(app, port=8080)
