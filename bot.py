#!env python3

import logging
from dccbot.app import get_app
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]%(message)s")

if __name__ == "__main__":
    app = get_app()
    if app["bot_manager"].config.get("http", {}).get("socket"):
        web.run_app(app, path=app["bot_manager"].config["http"]["socket"])
    else:
        web.run_app(
            app, host=app["bot_manager"].config.get("http", {}).get("host", "127.0.0.1"), port=app["bot_manager"].config.get("http", {}).get("port", 8080)
        )
