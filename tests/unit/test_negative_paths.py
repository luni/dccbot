"""Negative-path tests: malformed inputs, boundary values, and rejection paths."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from dccbot.app import IRCBotAPI
from dccbot.dcc_parsing import is_valid_filename, parse_dcc_accept, parse_dcc_send
from dccbot.ircbot import IRCBot
from dccbot.manager import IRCBotManager
from dccbot.transfers import get_incomplete_suffix, normalize_status, transfer_speeds


@pytest.fixture
def mock_bot_manager():
    """Create a mock bot manager."""
    manager = MagicMock()
    manager.config = {}
    manager.transfers = {}
    return manager


@pytest.fixture
def bot_factory(mock_bot_manager, loop_patch):
    """Factory to build IRCBot instances with shared defaults."""

    def _create_bot(
        *,
        server_config: dict | None = None,
        allowed_mimetypes: list[str] | None = None,
        max_file_size: int = 1_000_000,
        download_path: str = "/tmp/downloads",
        manager=None,
    ) -> IRCBot:
        base_config = {"nick": "testbot", "channels": []}
        if server_config:
            base_config.update(server_config)

        return IRCBot(
            server="irc.example.com",
            server_config=base_config,
            download_path=download_path,
            allowed_mimetypes=allowed_mimetypes,
            max_file_size=max_file_size,
            bot_manager=manager or mock_bot_manager,
        )

    return _create_bot


@pytest.fixture
def bot(bot_factory):
    """Default IRCBot instance for tests."""
    return bot_factory()


def _make_event(nick: str = "sender", arguments: list | None = None, target: str | None = None) -> MagicMock:
    event = MagicMock()
    event.source = MagicMock()
    event.source.nick = nick
    event.arguments = arguments if arguments is not None else []
    event.target = target
    return event


class TestDccParsingBoundaries:
    """Boundary and malformed-input cases for the DCC payload parsers."""

    def test_parse_dcc_send_port_boundaries(self):
        """Port 65535 is accepted, 65536 and negatives are rejected."""
        assert parse_dcc_send('SEND "f" 134744072 65535 1024') is not None
        assert parse_dcc_send('SEND "f" 134744072 65536 1024') is None
        assert parse_dcc_send('SEND "f" 134744072 -1 1024') is None

    def test_parse_dcc_send_size_boundaries(self):
        """Size must be a positive plain integer."""
        assert parse_dcc_send('SEND "f" 134744072 5000 1') is not None
        assert parse_dcc_send('SEND "f" 134744072 5000 0') is None
        assert parse_dcc_send('SEND "f" 134744072 5000 -100') is None
        assert parse_dcc_send('SEND "f" 134744072 5000 5.0') is None
        assert parse_dcc_send('SEND "f" 134744072 5000 0x10') is None

    def test_parse_dcc_send_token_boundaries(self):
        """Passive token must be a non-negative integer when present."""
        assert parse_dcc_send('SEND "f" 134744072 0 1024 0') is not None
        assert parse_dcc_send('SEND "f" 134744072 0 1024 -1') is None
        assert parse_dcc_send('SEND "f" 134744072 0 1024 abc') is None

    def test_parse_dcc_send_arity_and_garbage(self):
        """Too few/many parts, empty payload, and bare verb are rejected."""
        assert parse_dcc_send("") is None
        assert parse_dcc_send("SEND") is None
        assert parse_dcc_send('SEND "f" 134744072 5000') is None
        assert parse_dcc_send('SEND "f" 134744072 5000 1024 1 2') is None
        assert parse_dcc_send('SEND "f" 9999999999999999999999999 5000 1024') is None

    def test_parse_dcc_accept_boundaries(self):
        """ACCEPT port/position boundaries and arity are enforced."""
        assert parse_dcc_accept('ACCEPT "f" 65535 0') is not None
        assert parse_dcc_accept('ACCEPT "f" 65536 0') is None
        assert parse_dcc_accept('ACCEPT "f" 5000 -1') is None
        assert parse_dcc_accept('ACCEPT "f" 5000') is None
        assert parse_dcc_accept('ACCEPT "f" 5000 10 20 30') is None
        assert parse_dcc_accept("") is None

    @pytest.mark.parametrize("filename", ["", ".", "..", "a/b", "a\\b", "file:name", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b", "/etc/passwd"])
    def test_is_valid_filename_rejects(self, filename):
        """Filenames with separators or illegal characters are rejected."""
        assert is_valid_filename("/tmp/downloads", filename) is False

    @pytest.mark.parametrize("filename", ["file.mkv", "a file with spaces.bin", "日本語.txt", ".hidden", "..."])
    def test_is_valid_filename_accepts(self, filename):
        """Plain filenames including unicode and dotfiles are accepted."""
        assert is_valid_filename("/tmp/downloads", filename) is True


class TestTransferHelpers:
    """Normalization and speed helpers under weird input shapes."""

    def test_normalize_status_unknown_with_error(self):
        """Unknown status falls back to error when an error string is set."""
        assert normalize_status({"status": "bogus", "error": "boom"}) == "error"

    def test_normalize_status_completed_bool_not_counted(self):
        """A boolean `completed` flag does not map to the completed status."""
        assert normalize_status({"status": "bogus", "completed": True}) == "started"

    def test_normalize_status_completed_timestamp(self):
        """A completion timestamp (non-bool number) maps to completed."""
        assert normalize_status({"status": "bogus", "completed": time.time()}) == "completed"
        assert normalize_status({"status": "bogus", "completed": 1}) == "completed"

    def test_normalize_status_progress_fallbacks(self):
        """Bytes/connected imply in_progress; empty dict yields started."""
        assert normalize_status({"status": "bogus", "bytes_received": 5}) == "in_progress"
        assert normalize_status({"status": "bogus", "connected": True}) == "in_progress"
        assert normalize_status({}) == "started"

    def test_get_incomplete_suffix_rejects_non_string(self):
        """Only non-empty strings are valid suffixes."""
        assert get_incomplete_suffix({"incomplete_suffix": 123}) is None
        assert get_incomplete_suffix({"incomplete_suffix": []}) is None
        assert get_incomplete_suffix({"incomplete_suffix": ""}) is None
        assert get_incomplete_suffix({}) is None
        assert get_incomplete_suffix({"incomplete_suffix": ".part"}) == ".part"

    def test_transfer_speeds_degenerate_timestamps(self):
        """Zero/missing/future timestamps never divide by zero."""
        t = {"bytes_received": 1024, "start_time": 0, "last_progress_update": 100.0, "last_progress_bytes_received": 512}
        assert transfer_speeds(t, now=100.0) == (0, 0)

        t2 = {"bytes_received": 1024, "start_time": 200.0, "last_progress_update": 200.0, "last_progress_bytes_received": 0}
        assert transfer_speeds(t2, now=100.0) == (0, 0)


class TestManagerNegative:
    """Config validation and cleanup edge cases for IRCBotManager."""

    @pytest.mark.parametrize(
        "config",
        [
            [],
            {},
            {"servers": []},
            {"servers": "irc.example.com"},
            {"servers": {}, "http": "nope"},
            {"servers": {}, "http": {"host": 123}},
            {"servers": {}, "http": {"port": "6667"}},
            {"servers": {}, "dcc_ssl_cert": 123},
            {"servers": {}, "dcc_ssl_key": ["k"]},
            {"servers": {}, "server_idle_timeout": "30m"},
            {"servers": {}, "transfer_list_timeout": [86400]},
            {"servers": {}, "max_file_size": "1GB"},
            {"servers": {}, "allowed_mimetypes": "video/mp4"},
            {"servers": {"s": {"channels": "#notalist"}}},
            {"servers": {"s": {"channels": ["#ok", 123]}}},
            {"servers": {"s": {"rewrite_to_ssend": [123]}}},
            {"servers": {"s": {"also_join": "#notadict"}}},
            {"servers": {"s": {"also_join": {"#a": "#stringnotlist"}}}},
            {"servers": {"s": {"also_join": {"#a": [123]}}}},
        ],
    )
    def test_load_config_rejects_invalid_shapes(self, config, config_file_factory):
        """Structurally invalid configs raise ValueError."""
        with pytest.raises(ValueError):
            IRCBotManager(config_file_factory(config))

    def test_load_config_missing_file(self):
        """A missing config file propagates the OS error."""
        with pytest.raises(OSError):
            IRCBotManager("/nonexistent/path/config.json")

    @pytest.mark.asyncio
    async def test_cleanup_transfers_expires_missing_start_time(self, mock_bot_manager):
        """Transfers without a start_time are treated as expired."""
        manager = mock_bot_manager
        manager.transfer_list_timeout = 60
        manager.transfers = {"f.bin": [{"filename": "f.bin"}], "g.bin": [{"start_time": time.time()}]}

        await IRCBotManager._cleanup_transfers(manager)

        assert "f.bin" not in manager.transfers
        assert manager.transfers["g.bin"]

    @pytest.mark.asyncio
    async def test_cleanup_transfers_expires_non_numeric_start_time(self, mock_bot_manager):
        """A non-numeric start_time is treated as expired, not a crash."""
        manager = mock_bot_manager
        manager.transfer_list_timeout = 60
        manager.transfers = {"f.bin": [{"filename": "f.bin", "start_time": "not-a-number"}]}

        await IRCBotManager._cleanup_transfers(manager)

        assert "f.bin" not in manager.transfers

    @pytest.mark.asyncio
    async def test_cleanup_transfers_expires_malformed_values(self, mock_bot_manager):
        """Non-list values and non-dict items are treated as expired."""
        manager = mock_bot_manager
        manager.transfer_list_timeout = 60
        manager.transfers = {
            "notalist.bin": "oops",  # type: ignore
            "notdict.bin": ["oops"],  # type: ignore
            "good.bin": [{"start_time": time.time()}],
        }

        await IRCBotManager._cleanup_transfers(manager)

        assert "notalist.bin" not in manager.transfers
        assert "notdict.bin" not in manager.transfers
        assert manager.transfers["good.bin"]

    @pytest.mark.asyncio
    async def test_cancel_transfer_marks_cancelled_before_disconnect(self, config_file_factory):
        """The transfer must read 'cancelled' when disconnect fires the event."""
        config = config_file_factory({"servers": {"irc.example.com": {"nick": "b"}}})
        manager = IRCBotManager(config)
        transfer = {"filename": "f.bin", "status": "in_progress", "nick": "sender", "server": "irc.example.com"}
        statuses_at_disconnect = []
        dcc = MagicMock()
        dcc.disconnect.side_effect = lambda *args: statuses_at_disconnect.append(transfer["status"])
        bot = MagicMock()
        bot.current_transfers = {dcc: transfer}
        manager.bots = {"irc.example.com": bot}

        assert await manager.cancel_transfer("irc.example.com", "sender", "f.bin") is True
        assert statuses_at_disconnect == ["cancelled"]
        assert transfer["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_transfer_ignores_incomplete_records(self, config_file_factory):
        """Records missing nick/status keys do not match a cancel request."""
        config = config_file_factory({"servers": {"irc.example.com": {"nick": "b"}}})
        manager = IRCBotManager(config)
        bot = MagicMock()
        bot.current_transfers = {MagicMock(): {"filename": "f.bin"}}
        manager.bots = {"irc.example.com": bot}

        assert await manager.cancel_transfer("irc.example.com", "sender", "f.bin") is False

    @pytest.mark.asyncio
    async def test_cancel_transfer_tolerates_malformed_records(self, config_file_factory):
        """Corrupt records (None nick, non-dict, non-list) do not block cancel."""
        config = config_file_factory({"servers": {"irc.example.com": {"nick": "b"}}})
        manager = IRCBotManager(config)
        transfer = {"filename": "f.bin", "status": "in_progress", "nick": "sender", "server": "irc.example.com"}
        dcc = MagicMock()
        bot = MagicMock()
        bot.current_transfers = {
            MagicMock(): {"filename": "f.bin", "nick": None},  # None nick must not crash
            MagicMock(): "not-a-dict",  # type: ignore
            dcc: transfer,
        }
        manager.bots = {"irc.example.com": bot}
        manager.transfers = {"f.bin": {"not": "a list"}, "other.bin": ["junk"]}  # type: ignore

        assert await manager.cancel_transfer("irc.example.com", "sender", "f.bin") is True
        assert transfer["status"] == "cancelled"
        dcc.disconnect.assert_called_once()


class TestIrcbotNegative:
    """Rejection and edge paths in IRCBot event handlers."""

    def test_on_ctcp_unknown_dcc_verb(self, bot):
        """Unknown DCC verbs are logged and ignored."""
        bot.connection = MagicMock()
        bot.on_ctcp(bot.connection, _make_event(arguments=["DCC", "CHAT chat 123 456"]))
        bot.connection.ctcp_reply.assert_not_called()

    def test_on_ctcp_empty_arguments(self, bot):
        """CTCP events without arguments are ignored."""
        bot.connection = MagicMock()
        bot.on_ctcp(bot.connection, _make_event(arguments=[]))
        bot.connection.ctcp_reply.assert_not_called()

    def test_on_dcc_accept_malformed_payload(self, bot):
        """Unparseable ACCEPT payloads are ignored."""
        bot.connection = MagicMock()
        with patch.object(bot, "init_dcc_connection") as mock_init:
            bot.on_dcc_accept(bot.connection, _make_event(arguments=["DCC", "ACCEPT garbage"]))
        mock_init.assert_not_called()

    def test_on_dcc_accept_unknown_nick(self, bot):
        """ACCEPT for a nick not in the resume queue is ignored."""
        bot.connection = MagicMock()
        with patch.object(bot, "init_dcc_connection") as mock_init:
            bot.on_dcc_accept(bot.connection, _make_event(nick="stranger", arguments=["DCC", 'ACCEPT "f.bin" 5000 100']))
        mock_init.assert_not_called()

    def test_on_dcc_send_max_file_size_boundary(self, bot_factory, tmp_path):
        """size == max_file_size is accepted, max+1 is rejected."""
        bot = bot_factory(download_path=str(tmp_path), max_file_size=1024)
        bot.config["allow_private_ips"] = True
        bot.connection = MagicMock()

        with patch.object(bot, "init_dcc_connection") as mock_init:
            bot.on_dcc_send(bot.connection, _make_event(arguments=["DCC", 'SEND "f.bin" 134744072 5000 1024']), False)
            assert mock_init.call_count == 1

            bot.on_dcc_send(bot.connection, _make_event(arguments=["DCC", 'SEND "f.bin" 134744072 5000 1025']), False)
            assert mock_init.call_count == 1

    def test_on_dcc_send_private_ipv6_rejected(self, bot_factory, tmp_path):
        """ULA IPv6 peers are rejected without allow_private_ips."""
        bot = bot_factory(download_path=str(tmp_path))
        bot.connection = MagicMock()

        with patch.object(bot, "init_dcc_connection") as mock_init:
            bot.on_dcc_send(bot.connection, _make_event(arguments=["DCC", 'SEND "f.bin" fd00::1 5000 1024']), False)
        mock_init.assert_not_called()

    @pytest.mark.parametrize(
        "port_range,expected",
        [
            ("1000-2000", None),
            (5000, None),
            ({"low": 1000, "high": 2000}, None),
            ([0, 70000], None),
            ([5000, 1000], None),
            (["a", "b"], None),
            ([1024, 2048], (1024, 2048)),
        ],
    )
    def test_get_passive_dcc_config_port_range_validation(self, bot_factory, port_range, expected):
        """Malformed passive port ranges are ignored with a warning."""
        bot = bot_factory(server_config={"passive_dcc": True, "passive_dcc_port_range": port_range})
        _, _, result = bot._get_passive_dcc_config()
        assert result == expected

    @pytest.mark.parametrize("timeout", ["abc", None, [5]])
    def test_get_passive_dcc_timeout_invalid_falls_back(self, bot_factory, timeout):
        """Non-numeric passive timeouts fall back to the 60s default."""
        bot = bot_factory(server_config={"passive_dcc_timeout": timeout})
        assert bot._get_passive_dcc_timeout() == 60

    def test_resolve_channel_from_event_all_invalid(self, bot):
        """Candidates matching own nick or containing spaces yield None."""
        event = _make_event(arguments=["testbot", "has space"], target="testbot")
        assert bot._resolve_channel_from_event(event) is None
        event2 = _make_event(arguments=[], target=None)
        assert bot._resolve_channel_from_event(event2) is None

    @pytest.mark.asyncio
    async def test_cleanup_prunes_expired_resume_queues(self, bot):
        """Expired resume entries are pruned; fresh ones are kept."""
        bot.connection = MagicMock()
        stale = time.time() - 3600
        fresh = time.time()
        bot.resume_queue = {
            "old": [("1.2.3.4", 1, "f", "/tmp/f", 10, 0, False, False, stale)],
            "new": [("1.2.3.4", 1, "g", "/tmp/g", 10, 0, False, False, fresh)],
        }
        bot.passive_resume_queue = {
            ("old", 1): {"requested_time": stale},
            ("new", 2): {"requested_time": fresh},
        }

        await bot.cleanup(channel_idle_timeout=0, resume_timeout=30)

        assert bot.resume_queue["old"] == []
        assert len(bot.resume_queue["new"]) == 1
        assert ("old", 1) not in bot.passive_resume_queue
        assert ("new", 2) in bot.passive_resume_queue

    def test_on_privmsg_empty_arguments(self, bot):
        """PRIVMSG events without arguments are ignored safely."""
        bot.connection = MagicMock()
        bot.on_privmsg(bot.connection, _make_event(arguments=[]))

    def test_md5_completion_skips_non_numeric_completed(self, bot):
        """A corrupt 'completed' field does not crash MD5 matching."""
        bot.connection = MagicMock()
        bot.bot_manager.transfers = {"f.bin": [{"nick": "sender", "server": "irc.example.com", "completed": "done"}]}
        event = _make_event(arguments=["** Transfer Completed blah md5sum: 0123456789abcdef0123456789abcdef"])
        bot.on_privmsg(bot.connection, event)
        assert bot.bot_manager.transfers["f.bin"][0].get("md5") is None

    def test_md5_completion_tolerates_malformed_transfers(self, bot):
        """Non-list/non-dict transfer records do not crash MD5 matching."""
        bot.connection = MagicMock()
        bot.bot_manager.transfers = {
            "notalist.bin": "oops",  # type: ignore
            "notdict.bin": ["oops"],  # type: ignore
            "f.bin": [{"nick": "sender", "server": "irc.example.com", "completed": time.time()}],
        }
        event = _make_event(arguments=["** Transfer Completed blah md5sum: 0123456789abcdef0123456789abcdef"])
        bot.on_privmsg(bot.connection, event)
        assert bot.bot_manager.transfers["f.bin"][0]["md5"] == "0123456789abcdef0123456789abcdef"

    def test_pack_announcement_resets_malformed_record_list(self, bot):
        """A non-list transfers value is reset rather than crashing."""
        bot.connection = MagicMock()
        bot.bot_manager.transfers = {"f.bin": "oops"}  # type: ignore
        msg = '** Sending you pack #5 ("f.bin"). size:1, MD5:0123456789abcdef0123456789abcdef'
        bot.on_privmsg(bot.connection, _make_event(nick="sender", arguments=[msg]))

        transfers: dict[str, list[dict]] = bot.bot_manager.transfers  # type: ignore
        assert len(transfers["f.bin"]) == 1
        assert transfers["f.bin"][0]["md5"] == "0123456789abcdef0123456789abcdef"

    def test_on_dcc_send_tolerates_malformed_transfers(self, bot_factory, tmp_path):
        """Corrupt transfer records cannot crash duplicate-transfer detection."""
        bot = bot_factory(download_path=str(tmp_path))
        bot.config["allow_private_ips"] = True
        bot.connection = MagicMock()
        bot.bot_manager.transfers = {"f.bin": [{"no": "size"}, "junk"], "other.bin": "oops"}  # type: ignore

        with patch.object(bot, "init_dcc_connection") as mock_init:
            bot.on_dcc_send(bot.connection, _make_event(arguments=["DCC", 'SEND "f.bin" 134744072 5000 1024']), False)
        mock_init.assert_called_once()

    def test_pack_announcement_reannounce_updates_existing_record(self, bot):
        """A repeated pack announcement updates the pending record, not duplicates."""
        bot.connection = MagicMock()
        msg = '** Sending you pack #5 ("f.bin"). size:1, MD5:0123456789abcdef0123456789abcdef'
        event = _make_event(nick="sender", arguments=[msg])
        bot.on_privmsg(bot.connection, event)
        bot.on_privmsg(bot.connection, event)

        records = bot.bot_manager.transfers["f.bin"]
        assert len(records) == 1
        assert records[0]["md5"] == "0123456789abcdef0123456789abcdef"

    def test_register_transfer_skips_incomplete_pending_record(self, bot):
        """A pending record missing keys cannot crash transfer registration."""
        bot.bot_manager.transfers = {"f.bin": [{"peer_address": None}]}
        item = bot._register_transfer("f.bin", {"nick": "n", "server": "irc.example.com"}, time.time())
        assert item["nick"] == "n"
        assert len(bot.bot_manager.transfers["f.bin"]) == 2

    def test_local_file_state_getsize_failure_skips_file(self, bot_factory, tmp_path):
        """A file vanishing between checks is skipped rather than crashing."""
        bot = bot_factory(download_path=str(tmp_path))
        (tmp_path / "f.bin").write_bytes(b"1234")
        with patch("os.path.getsize", side_effect=OSError("gone")):
            state = bot._local_file_state("f.bin", 100)
        assert state.resume_path is None
        assert not state.is_complete
        assert not state.too_large

    @pytest.mark.asyncio
    async def test_cleanup_prunes_non_numeric_timestamps(self, bot):
        """Corrupt timestamps are pruned rather than raising TypeError."""
        bot.connection = MagicMock()
        bot.joined_channels = {"#bad": "not-a-time"}
        bot.resume_queue = {"nick": [("1.2.3.4", 1, "f", "/tmp/f", 10, 0, False, False, "bad-time")]}
        bot.passive_resume_queue = {("nick", 1): {"requested_time": "bad-time"}}

        await bot.cleanup(channel_idle_timeout=60, resume_timeout=30)

        assert "#bad" not in bot.joined_channels
        assert bot.resume_queue["nick"] == []
        assert ("nick", 1) not in bot.passive_resume_queue

    def test_expand_channels_also_join_case_insensitive(self, bot_factory):
        """also_join expansion matches channels case-insensitively."""
        bot = bot_factory(server_config={"nick": "b", "channels": [], "also_join": {"#a": ["#b"]}})
        expanded = bot._expand_channels(["#A"])
        assert expanded == {"#a": "#A", "#b": "#b"}


class TestApiNegative:
    """Malformed request and edge-input cases for the HTTP/WS API."""

    @pytest.mark.asyncio
    async def test_join_malformed_json_body(self, api_client):
        """A non-JSON body is rejected by the validation middleware."""
        client, _ = api_client
        resp = await client.post("/join", data="{not json", headers={"Content-Type": "application/json"})
        assert resp.status in (400, 422)

    @pytest.mark.asyncio
    async def test_msg_channels_wrong_type(self, api_client):
        """A non-list channels field is rejected."""
        client, _ = api_client
        resp = await client.post("/msg", json={"server": "irc.example.com", "user": "bob", "message": "hi", "channels": "#a"})
        assert resp.status in (400, 422)

    @pytest.mark.asyncio
    async def test_msg_whitespace_only_user(self, api_client):
        """Whitespace-only user passes length validation and is queued empty."""
        client, mock_bot_manager = api_client
        mock_bot = AsyncMock()
        mock_bot_manager.get_bot.return_value = mock_bot
        mock_bot_manager.config = {}

        resp = await client.post("/msg", json={"server": "irc.example.com", "user": " ", "message": "hi"})
        assert resp.status == 200
        assert mock_bot.queue_command.call_args[0][0]["user"] == ""

    @pytest.mark.asyncio
    async def test_cancel_empty_filename_rejected(self, api_client):
        """Empty required strings fail schema validation."""
        client, _ = api_client
        resp = await client.post("/cancel", json={"server": "s", "nick": "n", "filename": ""})
        assert resp.status in (400, 422)

    def test_clean_channel_list_normalization(self):
        """Empty entries are dropped, case normalized, '#' prefixed once."""
        assert IRCBotAPI._clean_channel_list(["", "  ", "#ok", "UPPER", "chan"]) == ["#ok", "#upper", "#chan"]

    def test_read_html_file_rejects_traversal(self):
        """Filenames resolving outside static/ raise 404."""
        api = IRCBotAPI(config_file="config.json", bot_manager=MagicMock())
        with pytest.raises(web.HTTPNotFound):
            api._read_html_file("../pyproject.toml")
        with pytest.raises(web.HTTPNotFound):
            api._read_html_file("definitely-missing.html")

    def test_build_transfer_snapshot_non_mapping(self):
        """A non-mapping transfers attribute yields an empty snapshot."""
        api = IRCBotAPI(config_file="config.json", bot_manager=MagicMock())
        api.bot_manager.transfers = "not-a-mapping"  # type: ignore
        assert api._build_transfer_snapshot() == []

    def test_build_transfer_snapshot_skips_malformed_records(self):
        """Non-list values and bad field types are skipped, not fatal."""
        api = IRCBotAPI(config_file="config.json", bot_manager=MagicMock())
        api.bot_manager.transfers = {
            "bad.bin": [{"bytes_received": "NaN", "start_time": time.time()}],
            "nolist.bin": None,  # type: ignore
            "good.bin": [{"nick": "n", "server": "s", "size": 10, "bytes_received": 1, "offset": 0, "start_time": time.time()}],
        }
        snapshot = api._build_transfer_snapshot()
        assert [t["filename"] for t in snapshot] == ["good.bin"]

    @pytest.mark.asyncio
    async def test_websocket_bare_slash_command(self, ws_session):
        """A bare '/' command returns an error response."""
        ws, _ = ws_session
        await ws.send_str("/")
        msg = await ws.receive_json()
        assert msg["status"] == "error"
