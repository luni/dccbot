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
from dccbot.transfers import get_incomplete_suffix

logger = logging.getLogger(__name__)


class TransferHandler:
    """Encapsulates DCC transfer data path behavior."""

    def __init__(self, bot: IRCBot) -> None:
        """Initialize transfer handler for a specific IRC bot instance."""
        self.bot = bot

    def _touch_channel_activity(self, transfer: dict) -> None:
        """Bump the activity timestamp for channels associated with this nick."""
        nick = transfer["nick"].lower()
        if nick in self.bot.bot_channel_map:
            now = time.time()
            for channel in self.bot.bot_channel_map[nick]:
                self.bot.joined_channels[channel] = now

    def _update_progress(self, transfer: dict) -> None:
        """Recalculate and log transfer progress/rate if thresholds are met."""
        now = time.time()
        percent = int(100 * (transfer["bytes_received"] + transfer["offset"]) / transfer["size"])
        if transfer["percent"] + 10 > percent and now - transfer["last_progress_update"] < 5:
            return

        transfer["percent"] = percent
        elapsed = now - transfer["start_time"]
        transfer_rate_avg = (transfer["bytes_received"] / elapsed) / 1024 if elapsed > 0 else 0
        elapsed = now - transfer["last_progress_update"]
        transferred = transfer["bytes_received"] - transfer["last_progress_bytes_received"]
        transfer_rate = (transferred / elapsed) / 1024 if elapsed > 0 else 0

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

    def _check_mime_or_disconnect(self, dcc: AioDCCConnection, transfer: dict, data: bytes) -> bool:
        """Validate the first chunk's MIME type or close the connection."""
        if transfer["bytes_received"] != 0 or transfer.get("offset") or not self.bot.allowed_mimetypes:
            return True

        mime_type = self.bot.mime_checker.from_buffer(data)
        if mime_type in self.bot.allowed_mimetypes:
            return True

        logger.warning("[%s] Reject %s: Invalid MIME type (%s)", transfer["nick"], transfer["filename"], mime_type)
        transfer["status"] = "error"
        transfer["error"] = f"Invalid MIME type ({mime_type})"
        transfer["connected"] = False
        dcc.disconnect()
        self.bot.current_transfers.pop(dcc, None)
        return False

    def _write_chunk_or_disconnect(self, dcc: AioDCCConnection, transfer: dict, data: bytes) -> bool:
        """Append the chunk to the file or mark the transfer as failed."""
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
            return False
        return True

    def _send_ack(self, dcc: AioDCCConnection, transfer: dict) -> None:
        """Send the appropriate 32- or 64-bit acknowledgement."""
        ack = transfer["bytes_received"] + transfer["offset"]
        if transfer["size"] >= 1024 * 1024 * 1024 * 4:
            dcc.send_bytes(struct.pack("!Q", ack))
        else:
            dcc.send_bytes(struct.pack("!I", ack))

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
            self._touch_channel_activity(transfer)
            self._update_progress(transfer)
            if not self._check_mime_or_disconnect(dcc, transfer, data):
                return
            if not self._write_chunk_or_disconnect(dcc, transfer, data):
                return

        transfer["bytes_received"] += len(data)
        self._send_ack(dcc, transfer)

    def _mark_complete(self, transfer: dict, file_path: str, transfer_rate: float) -> None:
        """Mark a transfer as complete, queue MD5 and rename incomplete file."""
        logger.info("[%s] Download %s complete - size: %d bytes, %.2f KB/s", transfer["nick"], transfer["filename"], transfer["size"], transfer_rate)
        transfer["completed"] = time.time()
        transfer["status"] = "completed"
        if transfer.get("md5"):
            asyncio.create_task(self.bot._add_md5_check_queue_item(transfer))

        incomplete_suffix = get_incomplete_suffix(self.bot.config)
        if incomplete_suffix and file_path.endswith(incomplete_suffix):
            target = file_path[: -len(incomplete_suffix)]
            try:
                os.rename(file_path, target)
                logger.info("Renamed downloaded file to %s", transfer["filename"])
                transfer["file_path"] = target
            except Exception as e:
                logger.error("Error renaming %s to %s: %s", file_path, target, e)

    def _report_size_mismatch(self, transfer: dict, file_size: int) -> None:
        """Record a size mismatch failure for a transfer."""
        logger.error("[%s] Download %s failed: size mismatch %d != %d", transfer["nick"], transfer["filename"], file_size, transfer["size"])
        if transfer["status"] != "error":
            transfer["status"] = "failed"
            transfer["error"] = f"size mismatch {file_size} != {transfer['size']}"

    def _report_missing_file(self, transfer: dict, file_path: str) -> None:
        """Record a missing output file failure."""
        logger.error("[%s] Download failed: %s does not exist", transfer["nick"], file_path)
        if transfer["status"] != "error":
            transfer["status"] = "error"
            transfer["error"] = f"[{transfer['nick']}] Download failed: {file_path} does not exist"

    def _finalize_transfer(self, dcc: AioDCCConnection, transfer: dict) -> None:
        """Check the output file and finalize the transfer state."""
        if transfer.get("status") == "cancelled":
            self.bot.current_transfers.pop(dcc, None)
            return

        file_path = transfer["file_path"]
        elapsed = time.time() - transfer["start_time"]
        transfer_rate = (transfer["bytes_received"] / elapsed) / 1024 if elapsed > 0 else 0

        if not os.path.exists(file_path):
            self._report_missing_file(transfer, file_path)
        else:
            file_size = os.path.getsize(file_path)
            if file_size != transfer["size"]:
                self._report_size_mismatch(transfer, file_size)
            else:
                self._mark_complete(transfer, file_path, transfer_rate)

        self.bot.current_transfers.pop(dcc, None)

    def on_dcc_disconnect(self, connection: AioDCCConnection, event: irc.client_aio.Event) -> None:
        """Handle DCC disconnect and finalize transfer metadata."""
        logger.debug("DCC connection lost: %s", event)
        dcc = connection
        if dcc not in self.bot.current_transfers:
            logger.debug("Received DCC disconnect from unknown connection")
            return

        transfer = self.bot.current_transfers[dcc]
        transfer["connected"] = False
        self._touch_channel_activity(transfer)
        self._finalize_transfer(dcc, transfer)
