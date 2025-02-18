#!env python3

import logging
from dccbot.app import get_app
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]%(message)s")

if __name__ == "__main__":
    web.run_app(get_app(), path="/tmp/dccbot.sock")
