#!env python3

import logging
import os

from aiohttp import web

from dccbot.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]%(message)s")

if __name__ == "__main__":
    app = create_app(os.path.join(os.path.dirname(__file__), "config.json"))
    http_config = app["bot_manager"].config.get("http", {})
    host = http_config.get("host", http_config.get("bind_addr", "127.0.0.1"))
    port = http_config.get("port", http_config.get("bind_port", 8080))
    if http_config.get("socket"):
        web.run_app(app, path=http_config["socket"])
    else:
        web.run_app(app, host=host, port=port)
