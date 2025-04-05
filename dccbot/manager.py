import hashlib
import json
import time
import asyncio
from dccbot.ircbot import IRCBot
import logging
from typing import Dict, Any, List
from aiohttp import web

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

    config_file: str
    config: Dict[str, Any]
    bots: Dict[str, IRCBot]
    server_idle_timeout: int
    channel_idle_timeout: int
    resume_timeout: int
    transfer_list_timeout: int
    md5_check_queue: asyncio.Queue
    transfers: Dict[str, List[Dict[str, Any]]]

    def __init__(self, config_file: str):
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
        self.bots: Dict[str, IRCBot] = {}
        self.server_idle_timeout = self.config.get("server_idle_timeout", 1800)  # 30 minutes
        self.channel_idle_timeout = self.config.get("channel_idle_timeout", 1800)  # 30 minutes
        self.resume_timeout = self.config.get("resume_timeout", 30)  # Timeout until ACCEPT response send by server
        self.transfer_list_timeout = self.config.get("transfer_list_timeout", 86400)  # 1 day
        self.md5_check_queue = asyncio.Queue()
        self.transfers = {}

    def load_config(self) -> dict:
        """Load the configuration from a JSON file.

        Returns the configuration as a dictionary.
        Raises a ValueError if the configuration is invalid.
        """
        try:
            with open(self.config_file, "r") as f:
                config = json.load(f)
            if "servers" not in config:
                raise ValueError("Missing 'servers' key in config")
            return config
        except Exception as e:
            logger.error("Error loading config: %s", e)
            raise

    async def get_bot(self, server: str) -> IRCBot:
        """Get an IRCBot instance for a server.

        If the server is not in the bot manager, a new IRCBot instance will
        be created with the server's configuration.

        Args:
            server: The server to get the IRCBot instance for.

        Returns:
            The IRCBot instance for the server.

        """
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
            await bot.connect()
        return self.bots[server]

    async def _cleanup_transfers(self):
        """Clean up the transfer list.

        This method is called periodically to clean up the transfer list.
        It removes all transfers that have been inactive for more than
        self.transfer_list_timeout seconds.

        """
        now = time.time()

        expired_transfer_names = []
        for filename, transfers in self.transfers.items():
            for transfer in list(transfers):
                if transfer.get("start_time", 0) + self.transfer_list_timeout < now:
                    transfers.remove(transfer)

            if not transfers:
                expired_transfer_names.append(filename)

        for filename in expired_transfer_names:
            del self.transfers[filename]

    async def _cleanup_bots(self):
        """Clean up idle servers and channels.

        This method is called periodically to clean up idle servers and channels.
        It checks each server for idleness and disconnects it if it is idle
        and the idle timeout is set.

        """
        now = time.time()

        idle_servers = []
        for server, bot in self.bots.items():
            if (
                not bot.joined_channels
                and not bot.current_transfers
                and bot.command_queue.empty()
                and self.server_idle_timeout > 0
                and bot.last_active + self.server_idle_timeout < now
            ):
                idle_servers.append(server)
            else:
                await bot.cleanup(self.server_idle_timeout, self.resume_timeout)

        for server in idle_servers:
            await self.bots[server].disconnect("Idle timeout")
            del self.bots[server]

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
            except Exception as e:
                # Log the exception and wait 10 seconds before trying again
                logger.exception(e)
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
            while data := f.read(8192):
                hasher.update(data)

        logger.info("MD5 for %s is %s", filename, hasher.hexdigest())
        return hasher.hexdigest()

    async def check_queue_processor(self, loop: asyncio.AbstractEventLoop, md5_check_queue: asyncio.Queue):
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
                logging.debug("Checking MD5 for %s", transfer_job['filename'])
                md5_hash = await loop.run_in_executor(None, IRCBotManager.get_md5, transfer_job["file_path"])

                for transfer in self.transfers.get(transfer_job["filename"], []):
                    if transfer["id"] == transfer_job["id"]:
                        transfer["file_md5"] = md5_hash
                md5_check_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(e)
                md5_check_queue.task_done()


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

    Returns:
        None

    """
    # Cancel the background task, which will allow it to exit cleanly
    app["cleanup_task"].cancel()
    app["queue_processor_task"].cancel()

    # Wait for the task to finish
    await app["cleanup_task"]
    await app["queue_processor_task"]
