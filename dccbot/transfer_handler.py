"""Transfer receive/disconnect handling for IRCBot."""

from __future__ import annotations

import asyncio
import logging
import os
import struct
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import irc.client_aio

    from dccbot.aiodcc import AioDCCConnection
    from dccbot.ircbot import IRCBot

logger = logging.getLogger(__name__)


class TransferHandler:
    """Encapsulates DCC transfer data path behavior."""

    def __init__(self, bot: IRCBot) -> None:
        """Initialize transfer handler for a specific IRC bot instance."""
        self.bot = bot

    def on_dccmsg(self, connection: AioDCCConnection, event: irc.client_aio.Event) -> None:
        """Handle incoming DCC data chunk."""
        dcc = connection
        if dcc not in self.bot.current_transfers:
            logger.debug("Received DCC message from unknown connection")
            return

        transfer = self.bot.current_transfers[dcc]
        transfer["connected"] = True
        transfer["status"] = "in_progress"
        data = event.arguments[0]

        if not transfer["completed"]:
            now = time.time()
            if transfer["nick"].lower() in self.bot.bot_channel_map:
                for channel in self.bot.bot_channel_map[transfer["nick"].lower()]:
                    self.bot.joined_channels[channel] = now

            percent = int(100 * (transfer["bytes_received"] + transfer["offset"]) / transfer["size"])
            if transfer["percent"] + 10 <= percent or now - transfer["last_progress_update"] >= 5:
                transfer["percent"] = percent
                elapsed_time = now - transfer["start_time"]
                transfer_rate_avg = (transfer["bytes_received"] / elapsed_time) / 1024 if elapsed_time > 0 else 0
                elapsed_time = now - transfer["last_progress_update"]
                transferred_bytes = transfer["bytes_received"] - transfer["last_progress_bytes_received"]
                transfer_rate = (transferred_bytes / elapsed_time) / 1024

                logger.info(
                    "[%s] Downloading %s %d%% @ %.2f KB/s / %.2f KB/s",
                    transfer["nick"],
                    transfer["filename"],
                    transfer["percent"],
                    transfer_rate,
                    transfer_rate_avg,
                )
                transfer["last_progress_update"] = now
                transfer["last_progress_bytes_received"] = transfer["bytes_received"]

            if transfer["bytes_received"] == 0 and not transfer.get("offset") and self.bot.allowed_mimetypes:
                mime_type = self.bot.mime_checker.from_buffer(data)
                if mime_type not in self.bot.allowed_mimetypes:
                    logger.warning("[%s] Reject %s: Invalid MIME type (%s)", transfer["nick"], transfer["filename"], mime_type)
                    transfer["status"] = "error"
                    transfer["error"] = f"Invalid MIME type ({mime_type})"
                    transfer["connected"] = False
                    dcc.disconnect()
                    self.bot.current_transfers.pop(dcc, None)
                    return

            try:
                with open(transfer["file_path"], "ab") as f:
                    f.write(data)
            except Exception as e:
                logger.error("Error writing to file %s: %s", transfer["file_path"], e)
                transfer["status"] = "error"
                transfer["error"] = f"Error writing to file {transfer['file_path']}: {e}"
                transfer["connected"] = False
                dcc.disconnect()
                self.bot.current_transfers.pop(dcc, None)
                return

        transfer["bytes_received"] += len(data)
        ack = transfer["bytes_received"] + transfer["offset"]
        if transfer["size"] >= 1024 * 1024 * 1024 * 4:
            dcc.send_bytes(struct.pack("!Q", ack))
        else:
            dcc.send_bytes(struct.pack("!I", ack))

    def on_dcc_disconnect(self, connection: AioDCCConnection, event: irc.client_aio.Event) -> None:
        """Handle DCC disconnect and finalize transfer metadata."""
        logger.debug("DCC connection lost: %s", event)
        dcc = connection
        if dcc not in self.bot.current_transfers:
            logger.debug("Received DCC disconnect from unknown connection")
            return

        transfer = self.bot.current_transfers[dcc]
        transfer["connected"] = False

        if transfer["nick"].lower() in self.bot.bot_channel_map:
            for channel in self.bot.bot_channel_map[transfer["nick"].lower()]:
                self.bot.joined_channels[channel] = time.time()

        file_path = transfer["file_path"]
        elapsed_time = time.time() - transfer["start_time"]
        transfer_rate = (transfer["bytes_received"] / elapsed_time) / 1024

        if not os.path.exists(file_path):
            logger.error("[%s] Download failed: %s does not exist", transfer["nick"], file_path)
            if transfer["status"] != "error":
                transfer["status"] = "error"
                transfer["error"] = f"[{transfer['nick']}] Download failed: {file_path} does not exist"
        else:
            file_size = os.path.getsize(file_path)
            if file_size != transfer["size"]:
                logger.error("[%s] Download %s failed: size mismatch %d != %d", transfer["nick"], transfer["filename"], file_size, transfer["size"])
                if transfer["status"] != "error":
                    transfer["status"] = "failed"
                    transfer["error"] = f"size mismatch {file_size} != {transfer['size']}"
            else:
                logger.info("[%s] Download %s complete - size: %d bytes, %.2f KB/s", transfer["nick"], transfer["filename"], file_size, transfer_rate)
                transfer["completed"] = time.time()
                transfer["status"] = "completed"
                if transfer.get("md5"):
                    loop = asyncio.get_event_loop()
                    loop.run_until_complete(self.bot._add_md5_check_queue_item(transfer))

                if self.bot.config.get("incomplete_suffix") and file_path.endswith(self.bot.config["incomplete_suffix"]):
                    target = file_path[: -len(self.bot.config["incomplete_suffix"])]
                    try:
                        os.rename(file_path, target)
                        logger.info("Renamed downloaded file to %s", transfer["filename"])
                        transfer["file_path"] = target
                    except Exception as e:
                        logger.error("Error renaming %s to %s: %s", file_path, target, e)

        self.bot.current_transfers.pop(dcc, None)
