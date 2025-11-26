import asyncio
import datetime
import json
import logging
import re
import time
from collections.abc import Mapping
from pathlib import Path

from aiohttp import web
from aiohttp_apispec import docs, request_schema, response_schema, setup_aiohttp_apispec, validation_middleware
from marshmallow import Schema, fields, validate

from dccbot.manager import IRCBotManager, cleanup_background_tasks, start_background_tasks

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class CancelTransferRequestSchema(Schema):
    """Schema for the /cancel_transfer endpoint."""

    server = fields.Str(required=True, metadata={"description": "IRC server address"})
    nick = fields.Str(required=True, metadata={"description": "Sender nickname (the user sending the file)"})
    filename = fields.Str(required=True, metadata={"description": "Filename of the transfer to cancel"})


class JoinRequestSchema(Schema):
    """Schema for the /join endpoint."""

    server = fields.Str(
        required=True,
        metadata={
            "description": "IRC server address",
        },
    )
    channel = fields.Str(
        required=False,
        metadata={
            "description": "Channel to join",
        },
    )
    channels = fields.List(
        fields.Str(),
        required=False,
        metadata={
            "description": "List of channels to join",
        },
    )


class PartRequestSchema(Schema):
    """Schema for the /part endpoint."""

    server = fields.Str(
        required=True,
        metadata={
            "description": "IRC server address",
        },
    )
    channel = fields.Str(
        required=False,
        metadata={
            "description": "Channel to part",
        },
    )
    channels = fields.List(
        fields.Str(),
        required=False,
        metadata={
            "description": "List of channels to join",
        },
    )
    reason = fields.Str(
        required=False,
        metadata={
            "description": "Reason for parting the channel",
        },
    )


class MsgRequestSchema(Schema):
    """Schema for the /msg endpoint."""

    server = fields.Str(
        required=True,
        metadata={
            "description": "IRC server address",
        },
    )
    user = fields.Str(
        required=True,
        metadata={
            "description": "User to send the message to",
        },
    )
    message = fields.Str(
        required=True,
        metadata={
            "description": "Message to send",
        },
    )
    channel = fields.Str(
        required=False,
        metadata={
            "description": "Channel to send the message to",
        },
    )
    channels = fields.List(
        fields.Str(),
        required=False,
        metadata={
            "description": "List of channels to send the message to",
        },
    )


class ChannelInfo(Schema):
    """Schema for the /info endpoint."""

    name = fields.Str()
    last_active = fields.DateTime()


class NetworkInfo(Schema):
    """Schema for the /info endpoint."""

    server = fields.Str()
    nickname = fields.Str()
    channels = fields.List(
        fields.Nested(ChannelInfo),
    )


class TransferInfo(Schema):
    """Schema for the /info endpoint."""

    server = fields.Str()
    filename = fields.Str()
    nick = fields.Str()
    host = fields.Str()
    size = fields.Int()
    received = fields.Int()
    speed = fields.Float()
    speed_avg = fields.Float()
    md5 = fields.Str(
        allow_none=True,
    )
    file_md5 = fields.Str(
        allow_none=True,
    )
    status = fields.Str(
        validate=validate.OneOf(["in_progress", "completed", "failed", "error", "cancelled"]),
    )
    error = fields.Str(
        allow_none=True,
    )
    resumed = fields.Bool()
    connected = fields.Bool()


class InfoResponseSchema(Schema):
    """Response Schema for the /info endpoint."""

    networks = fields.List(
        fields.Nested(NetworkInfo),
    )
    transfers = fields.List(
        fields.Nested(TransferInfo),
    )


class DefaultResponseSchema(Schema):
    """Default Response Schema."""

    message = fields.Str()
    status = fields.Str()


# Custom logging handler to send logs to WebSocket clients
class WebSocketLogHandler(logging.Handler):
    """Custom logging handler to send logs to connected WebSocket clients.

    Attributes:
        websockets (Set[web.WebSocketResponse]): Set of WebSocket
            connections to send log entries to.

    """

    websockets: set[web.WebSocketResponse]

    def __init__(self, websockets: set[web.WebSocketResponse]) -> None:
        """Initialize a WebSocketLogHandler.

        Args:
            websockets (Set[web.WebSocketResponse]): Set of WebSocket
                connections to send log entries to.

        """
        super().__init__()
        self.websockets = websockets

    def emit(self, record: logging.LogRecord) -> None:
        """Send a log entry to connected WebSocket clients.

        Args:
            record (logging.LogRecord): The log record to send.

        """
        log_entry = {
            "type": "log",
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "message": self.format(record),
        }
        for ws in list(self.websockets):
            if ws.closed:
                self.websockets.remove(ws)
                continue

            asyncio.create_task(ws.send_str(json.dumps(log_entry)))


class IRCBotAPI:
    """Main class for the IRC bot API."""

    def __init__(self, config_file: str, bot_manager: IRCBotManager | None = None) -> None:
        """Initialize an IRCBotAPI object.

        Args:
            config_file (str): The path to the JSON configuration file.
            bot_manager (IRCBotManager | None, optional): The bot manager to use.
                Defaults to None.

        """
        self.app = web.Application()
        self.app.middlewares.append(validation_middleware)
        self.bot_manager = bot_manager or IRCBotManager(config_file)
        self.app["bot_manager"] = self.bot_manager
        self.app.on_startup.append(start_background_tasks)
        self.app.on_cleanup.append(cleanup_background_tasks)
        self.websockets = set()
        self.static_dir = STATIC_DIR
        self.transfer_broadcast_task: asyncio.Task | None = None
        self.setup_routes()
        self.setup_apispec()

        ws_log_handler = WebSocketLogHandler(self.websockets)
        ws_log_handler.setFormatter(logging.Formatter("%(message)s"))

        logger.addHandler(ws_log_handler)
        ircbot_logger = logging.getLogger("dccbot.ircbot")
        ircbot_logger.addHandler(ws_log_handler)
        self.app.on_startup.append(self.start_transfer_broadcast)
        self.app.on_cleanup.append(self.stop_transfer_broadcast)

    async def start_transfer_broadcast(self, app: web.Application) -> None:
        """Start periodic transfer snapshots over WebSocket."""
        if not self.transfer_broadcast_task:
            self.transfer_broadcast_task = asyncio.create_task(self.broadcast_transfers())

    async def stop_transfer_broadcast(self, app: web.Application) -> None:
        """Stop periodic transfer snapshots over WebSocket."""
        if self.transfer_broadcast_task:
            self.transfer_broadcast_task.cancel()
            try:
                await self.transfer_broadcast_task
            except asyncio.CancelledError:
                pass
            self.transfer_broadcast_task = None

    async def broadcast_transfers(self) -> None:
        """Broadcast current transfer status to all websocket clients."""
        try:
            while True:
                await self._broadcast_transfers_to_clients()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def _broadcast_transfers_to_clients(self) -> None:
        if not self.websockets:
            return

        transfers = self._build_transfer_snapshot()
        message = json.dumps({"type": "transfers", "transfers": transfers})
        for ws in list(self.websockets):
            if ws.closed:
                self.websockets.discard(ws)
                continue
            try:
                await ws.send_str(message)
            except ConnectionResetError:
                self.websockets.discard(ws)

    def _build_transfer_snapshot(self) -> list[dict[str, object]]:
        """Collect current transfer information."""
        snapshot: list[dict[str, object]] = []
        now = time.time()
        transfers_data = getattr(self.bot_manager, "transfers", {})
        if not isinstance(transfers_data, Mapping):
            transfers_data = {}

        for filename, transfers in transfers_data.items():
            for transfer in transfers:
                transferred_bytes = transfer["bytes_received"]
                transfer_time = now - transfer["start_time"] if transfer["start_time"] else 0
                speed_avg = transferred_bytes / transfer_time / 1024 if transfer_time > 0 else 0

                recent_bytes = transfer["bytes_received"] - transfer["last_progress_bytes_received"]
                recent_duration = now - transfer["last_progress_update"]
                speed = (recent_bytes / recent_duration) / 1024 if recent_duration > 0 else 0

                snapshot.append({
                    "server": transfer["server"],
                    "filename": filename,
                    "nick": transfer["nick"],
                    "host": transfer["peer_address"] + ":" + str(transfer["peer_port"]),
                    "size": transfer["size"],
                    "received": transfer["bytes_received"] + transfer["offset"],
                    "speed": round(speed, 2),
                    "speed_avg": round(speed_avg, 2),
                    "md5": transfer.get("md5"),
                    "file_md5": transfer.get("file_md5"),
                    "status": transfer.get("status"),
                    "error": transfer.get("error"),
                    "resumed": transfer.get("offset", 0) > 0,
                    "connected": transfer.get("connected"),
                })
        return snapshot

    def _build_info_payload(self) -> dict[str, object]:
        """Build the payload returned by the /info endpoint."""
        response = {"networks": [], "transfers": self._build_transfer_snapshot()}
        for server, bot in self.bot_manager.bots.items():
            network_info = {"server": server, "nickname": bot.nick, "channels": []}

            for channel, last_active in bot.joined_channels.items():
                network_info["channels"].append({"name": channel, "last_active": last_active})

            response["networks"].append(network_info)
        return response

    async def _send_transfer_snapshot(self, ws: web.WebSocketResponse) -> None:
        """Send the latest transfer snapshot to a single websocket client."""
        transfers = self._build_transfer_snapshot()
        await ws.send_json({"type": "transfers", "transfers": transfers})

    async def handle_ws_command(self, command: str | None, args: list[str], ws: web.WebSocketResponse) -> None:
        """Handle a WebSocket command.

        Args:
            command (str): The command to handle.
            args (List[str]): The arguments for the command.
            ws (ClientWebSocketResponse): The WebSocket connection to send the
                response to.

        """
        try:
            logging.info("Received command from client: %s %s", command, args)
            if command == "help":
                command = None
                msg = "Available commands: part, join, msg, msgjoin, info"
                if len(args) > 0:
                    command = args.pop(0)

                if command in ("part", "join"):
                    msg = f"Usage: {command} <server> <channel> [<channel> ...]"
                elif command == "msg":
                    msg = f"Usage: {command} <server> <target> <message>"
                elif command == "msgjoin":
                    msg = f"Usage: {command} <server> <channel> <target> <message>"
                elif command:
                    msg = f"Unknown command: {command}"

                await ws.send_json({"status": "ok", "message": msg})
            elif command == "part":
                if len(args) < 2:
                    raise RuntimeError("Not enough arguments")
                server = args.pop(0)
                bot = await self.bot_manager.get_bot(server)
                await bot.queue_command({
                    "command": "part",
                    "channels": self._clean_channel_list(args),
                })
            elif command == "join":
                if len(args) < 2:
                    raise RuntimeError("Not enough arguments")
                server = args.pop(0)
                bot = await self.bot_manager.get_bot(server)
                await bot.queue_command({
                    "command": "join",
                    "channels": self._clean_channel_list(args),
                })
            elif command == "msg":
                if len(args) < 3:
                    raise RuntimeError("Not enough arguments")
                server = args.pop(0)
                bot = await self.bot_manager.get_bot(server)
                target = args.pop(0)
                await bot.queue_command({
                    "command": "send",
                    "user": target,
                    "message": " ".join(args),
                })
            elif command == "msgjoin":
                if len(args) < 4:
                    raise RuntimeError("Not enough arguments")
                server = args.pop(0)
                bot = await self.bot_manager.get_bot(server)
                channel = args.pop(0)
                target = args.pop(0)
                await bot.queue_command({
                    "command": "send",
                    "user": target,
                    "channels": [channel.lower().strip()],
                    "message": " ".join(args),
                })
            elif command == "info":
                await self._send_transfer_snapshot(ws)
        except RuntimeError as e:
            logger.error(str(e), exc_info=True)
            await ws.send_json({"status": "error", "message": str(e)})
        except Exception as e:
            logger.exception(e)

    # WebSocket handler
    async def websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """Handle a WebSocket connection.

        Establish a WebSocket connection and add it to the set of open connections.
        When a message is received from the client, log it. When the connection is
        closed (either by the client or due to an error), remove the connection from
        the set and log the event.

        Returns:
            web.WebSocketResponse: The WebSocket response object.

        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        # Add the new WebSocket connection to the set
        self.websockets.add(ws)

        ping_task = None
        try:
            # Send periodic ping frames to keep the connection alive
            async def send_ping() -> None:
                while True:
                    await asyncio.sleep(10)  # Send a ping every 10 seconds
                    if ws.closed:
                        break
                    await ws.ping()

            # Start the ping task
            ping_task = asyncio.create_task(send_ping())

            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    data = msg.data.strip()
                    if data.startswith("/"):  # Check if it's a command
                        parts = data.split()
                        command = parts[0][1:]  # Remove the leading '/'
                        args = parts[1:] if len(parts) > 1 else []
                        await self.handle_ws_command(command, args, ws)
                    else:
                        logging.info("Received message from client: %s", data)
                elif msg.type == web.WSMsgType.PONG:
                    logging.debug("Received pong from client")
                elif msg.type == web.WSMsgType.ERROR:
                    logging.error("WebSocket connection closed with exception: %s", ws.exception())
        finally:
            # Remove the WebSocket connection when it's closed
            try:
                self.websockets.remove(ws)
            except ValueError:
                pass
            if ping_task:
                ping_task.cancel()  # Stop the ping task

        return ws

    async def _return_static_html(self, request: web.Request) -> web.Response:
        """Serve HTML files located under the static directory."""
        filename = request.rel_url.path.split("/")[-1]
        fullpath = (self.static_dir / filename).resolve()
        if not str(fullpath).startswith(str(self.static_dir.resolve())) or not fullpath.exists():
            raise web.HTTPNotFound()

        with open(fullpath, encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")

    def setup_routes(self) -> None:
        """Set up routes for the aiohttp application."""
        self.app.router.add_post("/join", self.join)
        self.app.router.add_post("/part", self.part)
        self.app.router.add_post("/msg", self.msg)
        self.app.router.add_post("/shutdown", self.shutdown)
        self.app.router.add_post("/cancel", self.cancel)
        self.app.router.add_get("/info", self.info)
        self.app.router.add_get("/ws", self.websocket_handler)
        self.app.router.add_get("/log.html", self._return_static_html)
        self.app.router.add_get("/info.html", self._return_static_html)
        self.app.router.add_static("/static/", path=str(self.static_dir))

    def setup_apispec(self) -> None:
        """Configure aiohttp-apispec for API documentation."""
        setup_aiohttp_apispec(
            app=self.app,
            title="IRC Bot API",
            version="1.0.0",
            swagger_path="/swagger",  # URL for Swagger UI
            static_path="/static/swagger",  # Path for Swagger static files
        )

    @staticmethod
    def _clean_channel_list(l: list[str]) -> list[str]:
        """Normalize channel names by stripping, lowercasing, and prefixing '#'."""
        cleaned_channels: list[str] = []
        for channel in l:
            normalized = channel.lower().strip()
            if normalized and not normalized.startswith("#"):
                normalized = f"#{normalized}"
            cleaned_channels.append(normalized)
        return cleaned_channels

    @docs(
        tags=["IRC Commands"],
        summary="Join an IRC channel",
        description="Join a specified channel on a given IRC server.",
        responses={
            200: {"description": "Successfully joined the channel"},
            422: {"description": "Invalid request or missing parameters"},
        },
    )
    @request_schema(JoinRequestSchema())
    @response_schema(DefaultResponseSchema(), 200)
    async def join(self, request: web.Request) -> web.Response:
        """Handle a join request."""
        try:
            data = request["data"]
            if not data.get("channel") and not data.get("channels"):
                return web.json_response({"json": {"channel": ["Missing data for required field."]}}, status=422)

            bot = await self.bot_manager.get_bot(data["server"])
            await bot.queue_command({"command": "join", "channels": self._clean_channel_list(data.get("channels", [data.get("channel", "")]))})
            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.exception(e)
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    @docs(
        tags=["IRC Commands"],
        summary="Part an IRC channel",
        description="Leave a specified channel on a given IRC server.",
        responses={
            200: {"description": "Successfully left the channel"},
            422: {"description": "Invalid request or missing parameters"},
        },
    )
    @request_schema(PartRequestSchema())
    @response_schema(DefaultResponseSchema(), 200)
    async def part(self, request: web.Request) -> web.Response:
        """Handle a part request."""
        try:
            data = request["data"]
            if not data.get("channel") and not data.get("channels"):
                return web.json_response({"json": {"channel": ["Missing data for required field."]}}, status=422)

            bot = await self.bot_manager.get_bot(data["server"])
            await bot.queue_command({
                "command": "part",
                "channels": self._clean_channel_list(data.get("channels", [data.get("channel", "")])),
                "reason": data.get("reason"),
            })
            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.exception(e)
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    @docs(
        tags=["IRC Commands"],
        summary="Send a message to a user",
        description="Send a message to a specified user on a given IRC server.",
        responses={
            200: {"description": "Message sent successfully"},
            400: {"description": "Invalid request or missing parameters"},
        },
    )
    @request_schema(MsgRequestSchema())
    @response_schema(DefaultResponseSchema(), 200)
    async def msg(self, request: web.Request) -> web.Response:
        """Handle a message request."""
        try:
            data = request["data"]
            if not data.get("user") or not data.get("message"):
                return web.json_response({"status": "error", "message": "Missing user or message"}, status=400)

            bot = await self.bot_manager.get_bot(data["server"])
            channels = self._clean_channel_list(data.get("channels", [data.get("channel", [])]))

            # Check if we need to rewrite to ssend
            if (
                data["message"]
                and (
                    any(channel in bot.server_config.get("rewrite_to_ssend", []) for channel in channels)
                    or data["user"].lower().strip() in self.bot_manager.config.get("ssend_map", {})
                )
                and re.match(r"^xdcc (send|batch) ", data["message"], re.I)
            ):
                data["message"] = re.sub(r"^xdcc (send|batch) ", r"xdcc s\1 ", data["message"], re.I)

            await bot.queue_command({
                "command": "send",
                "channels": channels,
                "user": data["user"].lower().strip(),
                "message": data["message"].strip(),
            })
            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.exception(e)
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    @docs(
        tags=["Server Management"],
        summary="Shutdown the server",
        description="Shutdown the server and disconnect all IRC connections.",
        responses={
            200: {"description": "Server shutdown successfully"},
        },
    )
    @response_schema(DefaultResponseSchema(), 200)
    async def shutdown(self, request: web.Request) -> web.Response:
        """Handle a shutdown request."""
        logger.info("Shutting down server...")
        try:
            for bot in self.bot_manager.bots.values():
                await bot.disconnect("Shutting down")
            await request.app.shutdown()
            return web.json_response({"status": "ok"})
        except Exception as e:
            logger.exception(e)
            return web.json_response({"status": "error", "message": str(e)}, status=400)

    @docs(
        tags=["Server Management"],
        summary="Get server information",
        description="Retrieve information about all connected networks and active transfers.",
        responses={
            200: {"description": "Successfully retrieved server information", "schema": InfoResponseSchema},
            500: {"description": "Internal server error"},
        },
    )
    @response_schema(InfoResponseSchema(), 200)
    async def info(self, request: web.Request) -> web.Response:
        """Handle an information request."""
        try:
            response = self._build_info_payload()
            return web.json_response(response)
        except Exception as e:
            logger.exception(e)
            logger.error("Error in handle_info: %s", str(e))
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    @docs(
        tags=["Transfer Management"],
        summary="Cancel a running transfer",
        description="Cancel a running transfer by server and filename.",
        responses={
            200: {"description": "Successfully cancelled the transfer"},
            400: {"description": "Invalid request or error cancelling transfer"},
        },
    )
    @request_schema(CancelTransferRequestSchema())
    @response_schema(DefaultResponseSchema(), 200)
    async def cancel(self, request: web.Request) -> web.Response:
        """Cancel a running transfer by server, nick, and filename."""
        try:
            data = request["data"]
            server = data["server"]
            nick = data["nick"]
            filename = data["filename"]
            cancelled = await self.bot_manager.cancel_transfer(server, nick, filename)
            if cancelled:
                return web.json_response({"status": "ok", "message": "Transfer cancelled."})
            return web.json_response({"status": "error", "message": "Transfer not found or not running."}, status=400)
        except Exception as e:
            logger.exception(e)
            return web.json_response({"status": "error", "message": str(e)}, status=400)


def create_app(config_file: str) -> web.Application:
    """Create an aiohttp application with OpenAPI and Swagger UI support.

    Args:
        config_file (str): The path to the JSON configuration file.

    Returns:
        web.Application: The aiohttp application.

    """
    api = IRCBotAPI(config_file)  # pragma: no cover
    return api.app  # pragma: no cover
