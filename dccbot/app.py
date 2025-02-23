from aiohttp import web
from aiohttp_apispec import docs, marshal_with, setup_aiohttp_apispec, request_schema, response_schema, validation_middleware
from marshmallow import Schema, fields
import logging
from typing import List
from dccbot.ircbot import IRCBot
from dccbot.manager import IRCBotManager, start_background_tasks, cleanup_background_tasks
import time
import re
import asyncio

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


class IRCBotAPI:
    """Main class for the IRC bot API.

    Attributes:
        app (web.Application): The aiohttp application.
        bot_manager (IRCBotManager): The IRC bot manager.

    """

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
        self.setup_routes()
        self.setup_apispec()

    def setup_routes(self):
        """Set up routes for the aiohttp application."""
        self.app.router.add_post("/join", self.join)
        self.app.router.add_post("/part", self.part)
        self.app.router.add_post("/msg", self.msg)
        self.app.router.add_post("/shutdown", self.shutdown)
        self.app.router.add_get("/info", self.info)

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
