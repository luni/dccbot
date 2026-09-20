import asyncio
import contextlib
import functools
import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from dccbot.ircbot import IRCBot
from dccbot.ssl_util import get_or_create_dcc_cert
from dccbot.transfers import ensure_transfer_defaults

logger = logging.getLogger(__name__)


class IRCBotManager:
    """Manages IRCBots for different servers.

    Attributes:
        config_file (str): The path to the JSON configuration file.
        config (dict): The loaded configuration.
        bots (dict): A dictionary of IRCBot instances, keyed by server name.
        server_idle_timeout (int): The timeout for servers that are idle.
        channel_idle_timeout (int): The timeout for channels that are idle.
        resume_timeout (int): The timeout for resuming transfers.
        transfer_list_timeout (int): The timeout for the transfer list.
        md5_check_queue (Queue): A queue for MD5 checks.

    """

    def __init__(self, config_file: str) -> None:
        """Initialize an IRCBotManager object.

        The configuration is loaded from the file and stored in the
        `config` attribute. The idle timeouts for servers and channels are
        set to the values in the configuration, or to default values if
        not specified.

        Args:
            config_file (str): The path to the JSON configuration file.

        """
        self.config_file = config_file
        self.config = self.load_config()
        self.bots: dict[str, IRCBot] = {}
        self.server_idle_timeout = self.config.get("server_idle_timeout", 1800)  # 30 minutes
        self.channel_idle_timeout = self.config.get("channel_idle_timeout", 1800)  # 30 minutes
        self.resume_timeout = self.config.get("resume_timeout", 30)  # Timeout until ACCEPT response send by server
        self.transfer_list_timeout = self.config.get("transfer_list_timeout", 86400)  # 1 day
        self.md5_check_queue = asyncio.Queue()
        self.transfers: dict[str, list[dict[str, Any]]] = {}
        self._dcc_cert_cache_dir: Path | None = None
        self._dcc_cert_paths: tuple[str, str] | None = None
        self._dcc_cert_lock = threading.Lock()

    def get_or_create_dcc_cert(self) -> tuple[str, str]:
        """Return paths to the bot's DCC certificate and private key."""
        if self._dcc_cert_paths is not None:
            return self._dcc_cert_paths
        with self._dcc_cert_lock:
            if self._dcc_cert_paths is not None:
                return self._dcc_cert_paths
            self._dcc_cert_paths = get_or_create_dcc_cert(self.config, self._dcc_cert_cache_dir)
            return self._dcc_cert_paths

    async def cancel_transfer(self, server: str, nick: str, filename: str) -> bool:
        """Cancel a running transfer by server, bot_name, and filename.

        Returns True if cancelled, False if not found or not running.
        """
        server = server.lower()
        nick = nick.lower()

        # Find the bot
        bot = self.bots.get(server)
        if not bot:
            return False

        # Find the transfer in bot.current_transfers
        entry = next(
            (
                (conn, transfer)
                for conn, transfer in bot.current_transfers.items()
                if isinstance(transfer, dict)
                and transfer.get("filename") == filename
                and transfer.get("status") in ("started", "in_progress")
                and str(transfer.get("nick") or "").lower() == nick
            ),
            None,
        )
        if entry is None:
            return False
        dcc, transfer = entry

        # Mark cancelled before disconnecting so the dcc_disconnect event
        # finalizes the transfer as cancelled instead of failed/completed.
        self._mark_cancelled(transfer)
        try:
            dcc.disconnect("Cancelled by user")
        except Exception:
            logger.error("Failed to disconnect DCC connection", exc_info=True)

        bot.current_transfers.pop(dcc, None)

        # Update in manager.transfers if present
        records = self.transfers.get(filename, [])
        if not isinstance(records, list):
            return True
        for t in records:
            if not isinstance(t, dict):
                continue
            if str(t.get("server") or "").lower() == server and str(t.get("nick") or "").lower() == nick and t.get("status") in ("started", "in_progress"):
                self._mark_cancelled(t)
        return True

    @staticmethod
    def _mark_cancelled(transfer: dict[str, Any]) -> None:
        """Mark a transfer record as cancelled."""
        transfer["status"] = "cancelled"
        transfer["error"] = "Cancelled by user"
        transfer["connected"] = False

    def load_config(self) -> dict:
        """Load the configuration from a JSON file.

        Returns the configuration as a dictionary.
        Raises a ValueError if the configuration is invalid.
        """
        try:
            with open(self.config_file) as f:
                config = json.load(f)
            if "servers" not in config:
                raise ValueError("Missing 'servers' key in config")
            if not isinstance(config["servers"], dict):
                raise ValueError("'servers' must be a dictionary")

            self._normalize_config_contract(config)
            return config
        except Exception as e:
            logger.error("Error loading config: %s", e)
            raise

    @staticmethod
    def _normalize_server_config(server_config: dict[str, Any]) -> None:
        """Normalize case-insensitive values inside a single server config block."""
        if not isinstance(server_config, dict):
            return
        for key in ("rewrite_to_ssend", "channels"):
            value = server_config.get(key)
            if value is None:
                continue
            if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
                raise ValueError(f"'{key}' must be a list of strings")
            server_config[key] = [c.lower() for c in value]
        if "also_join" in server_config:
            also_join = server_config["also_join"]
            if not isinstance(also_join, dict):
                raise ValueError("'also_join' must be a dictionary")
            normalized: dict[str, list[str]] = {}
            for k, v in also_join.items():
                if not isinstance(k, str) or not isinstance(v, list) or not all(isinstance(c, str) for c in v):
                    raise ValueError("'also_join' must map strings to lists of strings")
                normalized[k.lower()] = [c.lower() for c in v]
            server_config["also_join"] = normalized

    @staticmethod
    def _normalize_config_contract(config: dict[str, Any]) -> None:
        """Normalize legacy config keys and validate key types."""
        IRCBotManager._normalize_servers(config)
        IRCBotManager._normalize_download_path(config)
        IRCBotManager._normalize_http_config(config)
        IRCBotManager._normalize_cert_keys(config)
        IRCBotManager._validate_transfer_keys(config)

    @staticmethod
    def _validate_transfer_keys(config: dict[str, Any]) -> None:
        """Validate types for transfer-related config keys."""
        for key in ("server_idle_timeout", "channel_idle_timeout", "resume_timeout", "transfer_list_timeout"):
            if key in config and not isinstance(config[key], (int, float)):
                raise ValueError(f"'{key}' must be a number")
        if "max_file_size" in config and not isinstance(config["max_file_size"], int):
            raise ValueError("'max_file_size' must be an integer")
        if "allowed_mimetypes" in config and config["allowed_mimetypes"] is not None and not isinstance(config["allowed_mimetypes"], list):
            raise ValueError("'allowed_mimetypes' must be a list")

    @staticmethod
    def _normalize_servers(config: dict[str, Any]) -> None:
        """Lowercase server-related keys and normalize each server config."""
        if "servers" in config and isinstance(config["servers"], dict):
            config["servers"] = {k.lower(): v for k, v in config["servers"].items()}

        if "ssend_map" in config and isinstance(config["ssend_map"], dict):
            config["ssend_map"] = {k.lower(): v for k, v in config["ssend_map"].items()}

        for server_config in config.get("servers", {}).values():
            IRCBotManager._normalize_server_config(server_config)

        if isinstance(config.get("default_server_config"), dict):
            IRCBotManager._normalize_server_config(config["default_server_config"])

    @staticmethod
    def _normalize_download_path(config: dict[str, Any]) -> None:
        """Handle the deprecated download_path key and apply the default."""
        if "default_download_path" not in config and "download_path" in config:
            config["default_download_path"] = config["download_path"]
            logger.warning("Config key 'download_path' is deprecated; use 'default_download_path'.")

        if "default_download_path" not in config:
            config["default_download_path"] = "./downloads"

    @staticmethod
    def _normalize_http_config(config: dict[str, Any]) -> None:
        """Normalize legacy http keys and validate http key types."""
        http_config = config.get("http")
        if http_config is None:
            return
        if not isinstance(http_config, dict):
            raise ValueError("'http' must be a dictionary if provided")

        for legacy, current in (("bind_addr", "host"), ("bind_port", "port")):
            if current not in http_config and legacy in http_config:
                http_config[current] = http_config[legacy]
                logger.warning("Config key 'http.%s' is deprecated; use 'http.%s'.", legacy, current)

        if "host" in http_config and not isinstance(http_config["host"], str):
            raise ValueError("'http.host' must be a string")
        if "port" in http_config and not isinstance(http_config["port"], int):
            raise ValueError("'http.port' must be an integer")

    @staticmethod
    def _normalize_cert_keys(config: dict[str, Any]) -> None:
        """Validate the DCC certificate config keys."""
        for key in ("dcc_ssl_cert", "dcc_ssl_key"):
            if key in config and not isinstance(config[key], str):
                raise ValueError(f"'{key}' must be a string")

    async def get_bot(self, server: str) -> IRCBot:
        """Get an IRCBot instance for a server.

        If the server is not in the bot manager, a new IRCBot instance will
        be created with the server's configuration.

        Args:
            server: The server to get the IRCBot instance for.

        Returns:
            The IRCBot instance for the server.

        """
        server = server.lower()

        if server not in self.bots:
            server_config = self.config["servers"].get(server, {})
            if not server_config and self.config.get("default_server_config") is not None:
                server_config = self.config["default_server_config"]

            if not server_config:
                raise ValueError(f"No configuration found for server: {server}")

            bot = IRCBot(
                server,
                server_config,
                self.config.get("default_download_path", "./downloads"),
                self.config.get("allowed_mimetypes"),
                self.config.get("max_file_size", 100 * 1024 * 1024),  # Default: 100 MB
                self,
            )
            self.bots[server] = bot
            try:
                await bot.connect()
            except Exception:
                self.bots.pop(server, None)
                raise
        return self.bots[server]

    async def _cleanup_transfers(self) -> None:
        """Clean up the transfer list.

        This method is called periodically to clean up the transfer list.
        It removes all transfers that have been inactive for more than
        self.transfer_list_timeout seconds.

        """
        now = time.time()

        expired_transfer_names = []
        for filename, transfers in self.transfers.items():
            if not isinstance(transfers, list):
                expired_transfer_names.append(filename)
                continue
            # Keep transfers that are still within the timeout window.
            # A non-numeric start_time is treated as expired rather than
            # raising TypeError on every cleanup pass.
            transfers[:] = [
                transfer
                for transfer in transfers
                if isinstance(transfer, dict)
                and isinstance(transfer.get("start_time"), (int, float))
                and transfer["start_time"] + self.transfer_list_timeout >= now
            ]
            if not transfers:
                expired_transfer_names.append(filename)

        for filename in expired_transfer_names:
            del self.transfers[filename]

    async def _cleanup_bots(self) -> None:
        """Clean up idle servers and channels.

        This method is called periodically to clean up idle servers and channels.
        It checks each server for idleness and disconnects it if it is idle
        and the idle timeout is set.

        """
        now = time.time()

        idle_servers = []
        for server, bot in list(self.bots.items()):
            if (
                not bot.joined_channels
                and not bot.current_transfers
                and bot.command_queue.empty()
                and self.server_idle_timeout > 0
                and isinstance(bot.last_active, (int, float))
                and bot.last_active + self.server_idle_timeout < now
            ):
                idle_servers.append(server)
            else:
                try:
                    await bot.cleanup(self.server_idle_timeout, self.resume_timeout)
                except Exception:
                    logger.exception("Error during cleanup for server %s", server)

        for server in idle_servers:
            bot = self.bots.pop(server, None)
            if bot is None:
                continue
            try:
                await bot.disconnect("Idle timeout")
            except Exception:
                logger.exception("Failed to disconnect idle bot %s", server)

    async def cleanup(self) -> None:
        """Clean up idle bots and channels.

        This is a background task that runs indefinitely. It periodically checks
        for idle servers and channels and cleans them up.

        Raises:
            Exception: If an unhandled exception occurs.

        """
        while True:
            try:
                await self._cleanup_bots()
                await self._cleanup_transfers()

                # Wait 1 second before checking again
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception:
                # Log the exception and wait 10 seconds before trying again
                logger.exception("Error in cleanup loop, retrying in 10s")
                await asyncio.sleep(10)

    @staticmethod
    def get_md5(filename: str) -> str:
        """Calculate the MD5 hash of a file.

        Args:
            filename: The path to the file to calculate the MD5 hash for.

        Returns:
            The MD5 hash of the file as a string of hexadecimal digits.

        """
        logger.info("Calculating MD5 for %s", filename)
        hasher = hashlib.md5()  # nosec
        with open(filename, "rb") as f:
            for chunk in iter(functools.partial(f.read, 1 << 20), b""):
                hasher.update(chunk)

        logger.info("MD5 for %s is %s", filename, hasher.hexdigest())
        return hasher.hexdigest()

    async def check_queue_processor(self, loop: asyncio.AbstractEventLoop, md5_check_queue: asyncio.Queue) -> None:
        """Run a loop that processes jobs from the md5_check_queue.

        For each job, calculate the MD5 hash of the file and update the
        corresponding transfer object in self.transfers with the result.

        If an exception is raised, log it and continue to the next job.

        The loop will exit if a CancelledError is raised.

        Args:
            loop (asyncio.AbstractEventLoop): The event loop to use.
            md5_check_queue (asyncio.Queue): The queue to process jobs from.

        """
        while True:
            try:
                transfer_job = await md5_check_queue.get()
                try:
                    logger.debug("Checking MD5 for %s", transfer_job["filename"])
                    md5_hash = await loop.run_in_executor(None, IRCBotManager.get_md5, transfer_job["file_path"])

                    for transfer in self.transfers.get(transfer_job["filename"], []):
                        # Skip records that clearly belong to another job.
                        if transfer.get("id") is not None and transfer["id"] != transfer_job["id"]:
                            continue
                        ensure_transfer_defaults(transfer_job["filename"], transfer)
                        if transfer["id"] == transfer_job["id"]:
                            transfer["file_md5"] = md5_hash
                finally:
                    # task_done pairs with the successful get() above; calling it
                    # in the outer except could raise ValueError when get() itself failed.
                    md5_check_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Failed to process MD5 check job")


async def start_background_tasks(app: web.Application) -> None:
    """Start the background task for cleaning up idle bots.

    This function is intended to be added as an on_startup handler for an aiohttp web application.

    Args:
        app: The aiohttp web application.

    """
    bot_manager: IRCBotManager = app["bot_manager"]
    app["cleanup_task"] = asyncio.create_task(bot_manager.cleanup())
    app["queue_processor_task"] = asyncio.create_task(bot_manager.check_queue_processor(asyncio.get_running_loop(), bot_manager.md5_check_queue))


async def cleanup_background_tasks(app: web.Application) -> None:
    """Cancel the background task for cleaning up idle bots.

    This function is intended to be added as an on_cleanup handler for an aiohttp web application.

    Args:
        app: The aiohttp web application.

    """
    # Cancel the background task, which will allow it to exit cleanly
    app["cleanup_task"].cancel()
    app["queue_processor_task"].cancel()

    # Wait for the tasks to finish
    with contextlib.suppress(asyncio.CancelledError):
        await app["cleanup_task"]
    with contextlib.suppress(asyncio.CancelledError):
        await app["queue_processor_task"]
