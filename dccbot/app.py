import asyncio
from dccbot.ircbot import IRCBot
from aiohttp import web
import logging
from dccbot.manager import IRCBotManager, start_background_tasks, cleanup_background_tasks
import time
from typing import List

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()


def _clean_channel_list(l: List[str]) -> List[str]:
    return [x.lower().strip() for x in l]


@routes.post("/join")
async def handle_join(request: web.Request) -> web.Response:
    """Handle a request to join a channel.

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
        if not data.get("channel"):
            return web.json_response({"status": "error", "message": "Missing channel"}, status=400)

        asyncio.create_task(bot.queue_command({"command": "join", "channels": _clean_channel_list(data.get("channels", [data.get("channel", "")]))}))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


@routes.post("/part")
async def handle_part(request: web.Request) -> web.Response:
    """Handle a request to part a channel.

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
        if not data.get("channel"):
            return web.json_response({"status": "error", "message": "Missing channel"}, status=400)

        asyncio.create_task(bot.queue_command({"command": "part", "channels": _clean_channel_list(data.get("channels", [data.get("channel", "")]))}))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)


@routes.post("/msg")
async def handle_msg(request: web.Request) -> web.Response:
    """Handle a request to send a message to a user.

    The request should contain the following JSON payload:
    {
        "server": str,
        "channel": str,
        "channels": List[str],
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
        if not data.get("user") or not data.get("message"):
            return web.json_response({"status": "error", "message": "Missing user or message"}, status=400)

        bot: IRCBot = await request.app["bot_manager"].get_bot(data["server"])
        channels = []

        if data.get("channels"):
            channels = _clean_channel_list(data["channels"])
        elif data.get("channel"):
            channels = _clean_channel_list([
                data["channel"],
            ])

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
        return web.json_response({"status": "error", "message": str(e)}, status=400)


@routes.post("/shutdown")
async def handle_shutdown(request: web.Request) -> web.Response:
    """Handle a request to shut down the server.

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


@routes.get("/info")
async def handle_info(request: web.Request):
    """Handle a request to get information about all connections.

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
        "transfers": [
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

        return web.json_response(response)
    except Exception as e:
        logging.exception(e)
        logger.error(f"Error in handle_info: {str(e)}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


def get_app():
    """Create an aiohttp application.

    The application will be configured with an IRCBotManager instance,
    the start_background_tasks and cleanup_background_tasks functions as
    on_startup and on_cleanup handlers, and the routes defined above.

    Returns:
        The aiohttp application.

    """
    app = web.Application()
    app["bot_manager"] = IRCBotManager("config.json")
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    app.router.add_routes(routes)
    return app
