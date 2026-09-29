"""Observable-effect tests: pin externally visible outputs and state.

These exist because mutation testing showed the suite exercised edge cases
without ever asserting the correct happy-path payload/state, letting whole
clusters of behavior-changing mutants survive.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dccbot.app import IRCBotAPI
from dccbot.ircbot import IRCBot
from dccbot.manager import IRCBotManager
from dccbot.transfers import create_pending_transfer


class TestTransferSnapshotContent:
    """_build_transfer_snapshot must expose every documented field."""

    def _api(self, transfers: dict) -> IRCBotAPI:
        manager = MagicMock()
        manager.transfers = transfers
        return IRCBotAPI(config_file="config.json", bot_manager=manager)

    def test_snapshot_contains_full_transfer_fields(self):
        """A well-formed transfer produces one snapshot row with all fields."""
        transfer = {
            "server": "irc.example.com",
            "nick": "sender",
            "peer_address": "10.1.2.3",
            "peer_port": 5000,
            "size": 1_000_000,
            "bytes_received": 250_000,
            "offset": 50,
            "start_time": time.time() - 100,
            "last_progress_update": time.time() - 10,
            "last_progress_bytes_received": 150_000,
            "md5": "expected-md5",
            "file_md5": "file-md5",
            "status": "in_progress",
            "connected": True,
        }
        api = self._api({"file.bin": [transfer]})

        snapshot = api._build_transfer_snapshot()

        assert len(snapshot) == 1
        row = snapshot[0]
        assert row["server"] == "irc.example.com"
        assert row["filename"] == "file.bin"
        assert row["nick"] == "sender"
        assert row["host"] == "10.1.2.3:5000"
        assert row["size"] == 1_000_000
        assert row["received"] == 250_050  # bytes_received + offset
        assert row["md5"] == "expected-md5"
        assert row["file_md5"] == "file-md5"
        assert row["status"] == "in_progress"
        assert row["connected"] is True
        assert row["resumed"] is True  # offset > 0
        assert row["error"] is None
        assert row["speed"] == pytest.approx(9.77, abs=0.5)  # 100 KB over ~10 s
        assert row["speed_avg"] == pytest.approx(2.44, abs=0.5)  # 250 KB over ~100 s

    def test_snapshot_host_empty_without_peer_info(self):
        """Missing peer address/port yields an empty host string."""
        api = self._api({"f.bin": [{"server": "s", "nick": "n", "size": 1}]})

        row = api._build_transfer_snapshot()[0]
        assert row["host"] == ""

    def test_snapshot_host_empty_when_port_missing(self):
        """A peer address without a port still yields an empty host."""
        api = self._api({"f.bin": [{"server": "s", "nick": "n", "size": 1, "peer_address": "10.0.0.1"}]})

        row = api._build_transfer_snapshot()[0]
        assert row["host"] == ""

    def test_snapshot_iterates_all_files(self):
        """Every file entry in the transfers map appears in the snapshot."""
        api = self._api({
            "a.bin": [{"server": "s", "nick": "n1", "size": 1}],
            "b.bin": [{"server": "s", "nick": "n2", "size": 2}],
        })

        snapshot = api._build_transfer_snapshot()

        assert {r["filename"] for r in snapshot} == {"a.bin", "b.bin"}
        assert {r["nick"] for r in snapshot} == {"n1", "n2"}


class TestGetBot:
    """IRCBotManager.get_bot observable behavior."""

    def _manager(self, config: dict) -> IRCBotManager:
        manager = IRCBotManager.__new__(IRCBotManager)
        manager.config = config
        manager.bots = {}
        return manager

    @pytest.mark.asyncio
    async def test_creates_and_caches_bot_for_configured_server(self):
        """A configured server yields a connected bot cached under its name."""
        manager = self._manager({"servers": {"irc.example.com": {"nick": "b", "channels": []}}})

        bot = MagicMock()
        bot.connect = AsyncMock()
        with patch("dccbot.manager.IRCBot", return_value=bot) as mock_cls:
            result = await manager.get_bot("IRC.EXAMPLE.COM")

        assert result is bot
        assert manager.bots["irc.example.com"] is bot
        bot.connect.assert_awaited_once()
        args = mock_cls.call_args[0]
        assert args[0] == "irc.example.com"
        assert args[1] == {"nick": "b", "channels": []}
        assert args[2] == "./downloads"  # default download path
        assert args[4] == 100 * 1024 * 1024  # default max_file_size
        assert args[5] is manager

    @pytest.mark.asyncio
    async def test_falls_back_to_default_server_config(self):
        """An unconfigured server uses default_server_config when present."""
        manager = self._manager({
            "servers": {},
            "default_server_config": {"nick": "d", "channels": ["#x"]},
            "default_download_path": "/srv/dl",
        })

        bot = MagicMock()
        bot.connect = AsyncMock()
        with patch("dccbot.manager.IRCBot", return_value=bot) as mock_cls:
            result = await manager.get_bot("unknown.example.com")

        assert result is bot
        args = mock_cls.call_args[0]
        assert args[1] == {"nick": "d", "channels": ["#x"]}
        assert args[2] == "/srv/dl"

    @pytest.mark.asyncio
    async def test_unknown_server_without_default_raises(self):
        """No server config and no default raises ValueError naming the server."""
        manager = self._manager({"servers": {"other": {"nick": "b"}}})

        with pytest.raises(ValueError, match="missing.example.com"):
            await manager.get_bot("MISSING.EXAMPLE.COM")

        assert manager.bots == {}

    @pytest.mark.asyncio
    async def test_connect_failure_removes_bot_and_reraises(self):
        """A failed connect must not leave a dead bot cached in self.bots."""
        manager = self._manager({"servers": {"s": {"nick": "b", "channels": []}}})

        bot = MagicMock()
        bot.connect = AsyncMock(side_effect=OSError("refused"))
        with patch("dccbot.manager.IRCBot", return_value=bot):
            with pytest.raises(OSError):
                await manager.get_bot("s")

        assert manager.bots == {}

    @pytest.mark.asyncio
    async def test_returns_existing_bot_without_reconnect(self):
        """A cached bot is returned as-is; connect is not called again."""
        manager = self._manager({"servers": {"s": {"nick": "b"}}})
        existing = MagicMock()
        manager.bots["s"] = existing

        with patch("dccbot.manager.IRCBot") as mock_cls:
            result = await manager.get_bot("S")

        assert result is existing
        mock_cls.assert_not_called()
        existing.connect.assert_not_called()


class TestManagerInitDefaults:
    """Constructor must apply documented config defaults."""

    def test_defaults_applied(self, config_file_factory):
        manager = IRCBotManager(config_file_factory({"servers": {}}))

        assert manager.server_idle_timeout == 1800
        assert manager.channel_idle_timeout == 1800
        assert manager.resume_timeout == 30
        assert manager.transfer_list_timeout == 86400
        assert manager.bots == {}
        assert manager.transfers == {}

    def test_config_overrides_applied(self, config_file_factory):
        manager = IRCBotManager(config_file_factory({
            "servers": {},
            "server_idle_timeout": 60,
            "channel_idle_timeout": 120,
            "resume_timeout": 5,
            "transfer_list_timeout": 3600,
        }))

        assert manager.server_idle_timeout == 60
        assert manager.channel_idle_timeout == 120
        assert manager.resume_timeout == 5
        assert manager.transfer_list_timeout == 3600


class TestRegisterTransferMerge:
    """_register_transfer merge-window and field-preservation semantics."""

    def _bot(self, records: list | None = None) -> IRCBot:
        manager = MagicMock()
        manager.transfers = {"f.bin": records if records is not None else []}
        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, "/tmp/dl", None, 1000, manager)
        return bot

    def _pending(self, **overrides) -> dict:
        item = create_pending_transfer("f.bin", "sender", "irc.example.com", md5="m5")
        item.update(overrides)
        return item

    def test_merges_recent_pending_preserving_md5_id_start(self, loop_patch):
        """A pending record within 30s merges, keeping md5/id/start_time."""
        now = time.time()
        pending = self._pending(start_time=now - 10, id="pid-1", md5="m5")
        bot = self._bot([pending])
        new_item = {"nick": "sender", "server": "irc.example.com", "peer_address": "10.0.0.1", "peer_port": 5000, "status": "started"}

        result = bot._register_transfer("f.bin", new_item, now)

        assert result is pending
        assert result["peer_address"] == "10.0.0.1"
        assert result["md5"] == "m5"
        assert result["id"] == "pid-1"
        assert result["start_time"] == now - 10
        assert len(bot.bot_manager.transfers["f.bin"]) == 1

    def test_no_merge_when_pending_expired(self, loop_patch):
        """A pending record older than 30s does not merge; a new entry is appended."""
        now = time.time()
        pending = self._pending(start_time=now - 60)
        bot = self._bot([pending])
        new_item = {"nick": "sender", "server": "irc.example.com", "peer_address": "10.0.0.1", "status": "started"}

        result = bot._register_transfer("f.bin", new_item, now)

        assert result is new_item
        assert bot.bot_manager.transfers["f.bin"] == [pending, new_item]
        assert "md5" not in new_item

    def test_no_merge_for_different_nick_or_server(self, loop_patch):
        """Pending records for a different nick or server are not merged."""
        now = time.time()
        other = self._pending(nick="other")
        bot = self._bot([other])
        new_item = {"nick": "sender", "server": "irc.example.com", "peer_address": "10.0.0.1", "status": "started"}

        bot._register_transfer("f.bin", new_item, now)

        assert bot.bot_manager.transfers["f.bin"] == [other, new_item]
        assert other["peer_address"] is None  # not merged with the new record

    def test_no_merge_when_pending_has_peer(self, loop_patch):
        """An in-flight record (peer_address set) is never treated as pending."""
        now = time.time()
        active = self._pending(start_time=now - 5, peer_address="10.9.9.9")
        bot = self._bot([active])
        new_item = {"nick": "sender", "server": "irc.example.com", "peer_address": "10.0.0.1", "status": "started"}

        bot._register_transfer("f.bin", new_item, now)

        assert bot.bot_manager.transfers["f.bin"] == [active, new_item]


class TestPassiveDccDownloadPath:
    """init_passive_dcc_connection path shaping is externally observable."""

    @pytest.mark.asyncio
    async def test_incomplete_suffix_appended_to_download_path(self, tmp_path):
        """Without explicit file_path the configured suffix is appended."""
        manager = MagicMock()
        manager.transfers = {}
        manager.config = {"incomplete_suffix": ".part", "passive_dcc_timeout": 0}
        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, str(tmp_path), None, 1000, manager)
        bot.connection = MagicMock()
        mock_dcc = MagicMock()
        mock_dcc.listen = AsyncMock()
        mock_dcc.localaddress = "127.0.0.1"
        mock_dcc.localport = 5000

        with patch.object(bot, "dcc", return_value=mock_dcc), patch.object(bot.loop, "create_task") as mock_create_task:
            bot.init_passive_dcc_connection("sender", "f.bin", 100)
            coro = mock_create_task.call_args[0][0]
            await coro

        transfer = next(iter(bot.current_transfers.values()))
        assert transfer["file_path"].endswith("f.bin.part")

    @pytest.mark.asyncio
    async def test_explicit_file_path_not_suffixed(self, tmp_path):
        """A resume with an explicit file_path keeps that exact path."""
        manager = MagicMock()
        manager.transfers = {}
        manager.config = {"incomplete_suffix": ".part", "passive_dcc_timeout": 0}
        bot = IRCBot("irc.example.com", {"nick": "b", "channels": []}, str(tmp_path), None, 1000, manager)
        bot.connection = MagicMock()
        mock_dcc = MagicMock()
        mock_dcc.listen = AsyncMock()
        mock_dcc.localaddress = "127.0.0.1"
        mock_dcc.localport = 5000

        explicit = str(tmp_path / "resume-target.bin")
        with patch.object(bot, "dcc", return_value=mock_dcc), patch.object(bot.loop, "create_task") as mock_create_task:
            bot.init_passive_dcc_connection("sender", "f.bin", 100, file_path=explicit, offset=10)
            coro = mock_create_task.call_args[0][0]
            await coro

        transfer = next(iter(bot.current_transfers.values()))
        assert transfer["file_path"] == explicit
