"""Command line entry point for dccbot."""

import argparse
import logging
import os

from aiohttp import web

from dccbot.app import create_app


def main(argv: list[str] | None = None) -> None:
    """Start the dccbot aiohttp application."""
    parser = argparse.ArgumentParser(prog="dccbot", description="IRC XDCC download bot")
    parser.add_argument("--config", default="config.json", help="path to the configuration file (default: ./config.json)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]%(message)s")

    app = create_app(os.path.abspath(args.config))
    # bind_addr/bind_port have already been normalized to host/port by
    # IRCBotManager._normalize_http_config.
    http_config = app["bot_manager"].config.get("http", {})
    if http_config.get("socket"):
        web.run_app(app, path=http_config["socket"])
    else:
        web.run_app(app, host=http_config.get("host", "127.0.0.1"), port=http_config.get("port", 8080))


if __name__ == "__main__":
    main()
