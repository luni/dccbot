from aiohttp import web, ClientWebSocketResponse
from aiohttp_apispec import docs, marshal_with, setup_aiohttp_apispec, request_schema, response_schema, validation_middleware
from marshmallow import Schema, fields
import logging
from typing import List, Set
from dccbot.ircbot import IRCBot
from dccbot.manager import IRCBotManager, start_background_tasks, cleanup_background_tasks
import time
import datetime
import re
import asyncio
import json

logger = logging.getLogger(__name__)


# Define Marshmallow Schemas for Request/Response Validation
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
    status = fields.Str()
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
        websockets (Set[ClientWebSocketResponse]): Set of WebSocket
            connections to send log entries to.

    """

    websockets: Set[ClientWebSocketResponse]

    def __init__(self, websockets: Set[ClientWebSocketResponse]):
        """Initialize a WebSocketLogHandler.

        Args:
            websockets (Set[ClientWebSocketResponse]): Set of WebSocket
                connections to send log entries to.

        """
        super().__init__()
        self.websockets = websockets

    def emit(self, record: logging.LogRecord):
        """Send a log entry to connected WebSocket clients.

        Args:
            record (logging.LogRecord): The log record to send.

        """
        log_entry = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "message": self.format(record),
        }
        for ws in list(self.websockets):
            try:
                if ws.closed:
                    self.websockets.remove(ws)
                    continue

                asyncio.create_task(ws.send_str(json.dumps(log_entry)))
            except Exception as e:
                pass


class IRCBotAPI:
    """Main class for the IRC bot API.

    Attributes:
        app (web.Application): The aiohttp application.
        bot_manager (IRCBotManager): The IRC bot manager.

    """

    app: web.Application
    websockets: Set[ClientWebSocketResponse]

    def __init__(self, config_file: str):
        """Initialize an IRCBotAPI object.

        Args:
            config_file (str): The path to the JSON configuration file.

        """
        self.app = web.Application()
        self.app.middlewares.append(validation_middleware)
        self.bot_manager = IRCBotManager(config_file)
        self.app["bot_manager"] = self.bot_manager
        self.app.on_startup.append(start_background_tasks)
        self.app.on_cleanup.append(cleanup_background_tasks)
        self.websockets = set()
        self.setup_routes()
        self.setup_apispec()

        ws_log_handler = WebSocketLogHandler(self.websockets)
        ws_log_handler.setFormatter(logging.Formatter("%(message)s"))

        logger.addHandler(ws_log_handler)
        ircbot_logger = logging.getLogger("dccbot.ircbot")
        ircbot_logger.addHandler(ws_log_handler)

    async def handle_ws_command(self, command: str, args: List[str], ws: ClientWebSocketResponse):
        """Handle a WebSocket command.

        Args:
            command (str): The command to handle.
            args (List[str]): The arguments for the command.
            ws (ClientWebSocketResponse): The WebSocket connection to send the
                response to.

        """
        try:
            logging.info(f"Received command from client: {command} {args}")
            if command == "help":
                await ws.send_json({"status": "ok", "message": "Available commands: part, join, msg"})
            elif command == "part":
                if len(args) < 2:
                    raise RuntimeError("Not enough arguments")
                server = args.pop(0)
                bot: IRCBot = await self.app["bot_manager"].get_bot(server)
                await bot.queue_command({
                    "command": "part",
                    "channels": self._clean_channel_list(args),
                })
            elif command == "join":
                if len(args) < 2:
                    raise RuntimeError("Not enough arguments")
                server = args.pop(0)
                bot: IRCBot = await self.app["bot_manager"].get_bot(server)
                await bot.queue_command({
                    "command": "join",
                    "channels": self._clean_channel_list(args),
                })
            elif command == "msg":
                if len(args) < 3:
                    raise RuntimeError("Not enough arguments")
                server = args.pop(0)
                bot: IRCBot = await self.app["bot_manager"].get_bot(server)
                target = args.pop(0)
                await bot.queue_command({
                    "command": "msg",
                    "user": target,
                    "message": " ".join(args),
                })
        except RuntimeError as e:
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

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    data = msg.data.strip()
                    if data.startswith("/"):  # Check if it's a command
                        parts = data.split()
                        command = parts[0][1:]  # Remove the leading '/'
                        args = parts[1:] if len(parts) > 1 else []
                        await self.handle_ws_command(command, args, ws)
                    else:
                        logging.info(f"Received message from client: {data}")
                elif msg.type == web.WSMsgType.ERROR:
                    logging.error(f"WebSocket connection closed with exception: {ws.exception()}")
        finally:
            # Remove the WebSocket connection when it's closed
            try:
                self.websockets.remove(ws)
            except Exception as e:
                pass

        return ws

    async def _return_static_html(self, request: web.Request) -> web.Response:
        """Return the contents of index.html as a text/html response.

        This endpoint serves the HTML for the WebSocket log viewer.
        """
        # use request uri to get the filename
        filename = request.rel_url.path.split("/")[-1]

        with open(f"static/{filename}", "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")

    def setup_routes(self):
        """Set up routes for the aiohttp application."""
        self.app.router.add_post("/join", self.join)
        self.app.router.add_post("/part", self.part)
        self.app.router.add_post("/msg", self.msg)
        self.app.router.add_post("/shutdown", self.shutdown)
        self.app.router.add_get("/info", self.info)
        self.app.router.add_get("/ws", self.websocket_handler)
        self.app.router.add_get("/log.html", self._return_static_html)
        self.app.router.add_get("/info.html", self._return_static_html)

    def setup_apispec(self):
        """Configure aiohttp-apispec for API documentation."""
        setup_aiohttp_apispec(
            app=self.app,
            title="IRC Bot API",
            version="1.0.0",
            swagger_path="/swagger",  # URL for Swagger UI
            static_path="/static/swagger",  # Path for Swagger static files
        )

    @staticmethod
    def _clean_channel_list(l: List[str]) -> List[str]:
        """Clean a list of channel names by stripping and lowercasing them."""
        return [x.lower().strip() for x in l]

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

            bot: IRCBot = await request.app["bot_manager"].get_bot(data["server"])
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

            bot: IRCBot = await request.app["bot_manager"].get_bot(data["server"])
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

            bot_manager = request.app["bot_manager"]
            bot: IRCBot = await bot_manager.get_bot(data["server"])
            channels = []

            if data.get("channels"):
                channels = self._clean_channel_list(data["channels"])
            elif data.get("channel"):
                channels = self._clean_channel_list([data["channel"]])

            # Check if we need to rewrite to ssend
            if (
                data["message"]
                and (
                    any(channel in bot.server_config.get("rewrite_to_ssend", []) for channel in channels)
                    or data["user"].lower().strip() in bot_manager.config.get("ssend_map", {})
                )
                and re.match(r"^xdcc (send|batch) ", data["message"], re.I)
            ):
                data["message"] = re.sub(r"^xdcc (send|batch) ", r"xdcc s\1 ", data["message"], re.I)

            asyncio.create_task(
                bot.queue_command({
                    "command": "send",
                    "channels": channels,
                    "user": data["user"].lower().strip(),
                    "message": data["message"].strip(),
                })
            )
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
            for bot in request.app["bot_manager"].bots.values():
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
    @marshal_with(InfoResponseSchema, 200)
    @response_schema(InfoResponseSchema(), 200)
    async def info(self, request: web.Request):
        """Handle an information request."""
        try:
            bot_manager: IRCBotManager = request.app["bot_manager"]
            response = {"networks": [], "transfers": []}

            # Gather information about all networks and channels
            for server, bot in bot_manager.bots.items():
                network_info = {"server": server, "nickname": bot.nick, "channels": []}

                # Add channel information
                for channel, last_active in bot.joined_channels.items():
                    network_info["channels"].append({"name": channel, "last_active": last_active})

                response["networks"].append(network_info)

            for filename, transfers in bot_manager.transfers.items():
                for transfer in transfers:
                    now = time.time()

                    transferred_bytes = transfer["bytes_received"]
                    transfer_time = now - transfer["start_time"] if transfer["start_time"] else 0
                    speed_avg = transferred_bytes / transfer_time / 1024 if transfer_time > 0 else 0

                    transferred_bytes = transfer["bytes_received"] - transfer["last_progress_bytes_received"]
                    transfer_time = now - transfer["last_progress_update"]
                    speed = (transferred_bytes / transfer_time) / 1024

                    transfer_info = {
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
                        "status": "completed" if transfer.get("completed") else "in_progress",
                        "connected": transfer.get("connected"),
                    }
                    response["transfers"].append(transfer_info)

            return web.json_response(response)  # Return the response
        except Exception as e:
            logger.exception(e)
            logger.error(f"Error in handle_info: {str(e)}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)


def create_app(config_file: str) -> web.Application:
    """Create an aiohttp application with OpenAPI and Swagger UI support.

    Args:
        config_file (Optional[str]): The path to the JSON configuration file.

    Returns:
        web.Application: The aiohttp application.

    """
    api = IRCBotAPI(config_file)
    return api.app
