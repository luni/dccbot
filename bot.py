#!env python3

import logging
import os

from aiohttp import web

from dccbot.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]%(message)s")

if __name__ == "__main__":
    app = create_app(os.path.join(os.path.dirname(__file__), "config.json"))
    if app["bot_manager"].config.get("http", {}).get("socket"):
        web.run_app(app, path=app["bot_manager"].config["http"]["socket"])
    else:
        web.run_app(
            app, host=app["bot_manager"].config.get("http", {}).get("host", "127.0.0.1"), port=app["bot_manager"].config.get("http", {}).get("port", 8080)
        )
