#!env python3

import logging
import os

from aiohttp import web

from dccbot.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]%(message)s")

if __name__ == "__main__":
    app = create_app(os.path.join(os.path.dirname(__file__), "config.json"))
    # bind_addr/bind_port have already been normalized to host/port by
    # IRCBotManager._normalize_http_config.
    http_config = app["bot_manager"].config.get("http", {})
    if http_config.get("socket"):
        web.run_app(app, path=http_config["socket"])
    else:
        web.run_app(app, host=http_config.get("host", "127.0.0.1"), port=http_config.get("port", 8080))
