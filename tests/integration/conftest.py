"""Shared fixtures for integration tests.

Provides the toxiproxy fixtures used by the live fault-injection tests.
toxiproxy is started by ``make irc-up`` (and CI) on admin port 8474;
two proxies are pre-configured:

- ``irc-proxy``: 127.0.0.1:16667 -> 127.0.0.1:6667 (InspIRCd)
- ``dcc-proxy``: 127.0.0.1:20001 -> 127.0.0.1:20099 (test peer server)
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from dccbot.ircbot import IRCBot
from dccbot.manager import IRCBotManager

# Local InspIRCd server for testing
TEST_IRC_SERVER = "localhost"
TEST_PORT = 6667

TOXIPROXY_API = "http://localhost:8474"

PROXIES = {
    "irc-proxy": ("127.0.0.1:16667", "127.0.0.1:6667"),
    "dcc-proxy": ("127.0.0.1:20001", "127.0.0.1:20099"),
}

ToxicAdder = Callable[..., None]


def toxi_request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
    """Call the toxiproxy HTTP API and return (status, body)."""
    req = urllib.request.Request(
        f"{TOXIPROXY_API}{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except OSError as e:
        return -1, str(e)


def _toxiproxy_available() -> bool:
    try:
        with urllib.request.urlopen(f"{TOXIPROXY_API}/version", timeout=2) as resp:
            return resp.status == 200
    except OSError:
        return False


@pytest.fixture(scope="session")
def toxiproxy() -> Generator[str]:
    """Provide the toxiproxy admin URL with proxies configured.

    Skips when toxiproxy isn't running so integration tests can still
    run against a bare ircd without the fault-injection sidecar.
    """
    if not _toxiproxy_available():
        pytest.skip("toxiproxy not running on :8474 (started by make irc-up)")
    for name, (listen, upstream) in PROXIES.items():
        toxi_request("DELETE", f"/proxies/{name}")
        status, body = toxi_request("POST", "/proxies", {"name": name, "listen": listen, "upstream": upstream})
        assert status == 201, body
    yield TOXIPROXY_API
    toxi_request("POST", "/reset")


@pytest.fixture()
def add_toxic(toxiproxy: str) -> Generator[ToxicAdder]:
    """Add a toxic to a proxy; all toxics are removed after the test."""

    def _add(proxy: str, name: str, toxic_type: str, attributes: dict[str, Any], stream: str = "downstream") -> None:
        status, body = toxi_request(
            "POST",
            f"/proxies/{proxy}/toxics",
            {
                "name": name,
                "type": toxic_type,
                "stream": stream,
                "toxicity": 1.0,
                "attributes": attributes,
            },
        )
        assert status == 200, body

    yield _add
    toxi_request("POST", "/reset")


@pytest.fixture
def unique_nick() -> str:
    """Generate a unique IRC nickname for testing."""
    return f"dccbot_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


@pytest.fixture
def irc_bot_manager(tmp_path) -> IRCBotManager:
    """Create an IRCBotManager with a temporary config file."""
    import json

    download_path = tmp_path / "downloads"
    download_path.mkdir(parents=True, exist_ok=True)

    config = {
        "servers": {},
        "default_download_path": str(download_path),
        "allowed_mimetypes": None,
        "max_file_size": 1073741824,
        "server_idle_timeout": 60,
        "channel_idle_timeout": 60,
        "resume_timeout": 30,
        "transfers": {},
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config))

    manager = IRCBotManager(str(config_file))
    return manager


@pytest.fixture
def irc_bot_factory(irc_bot_manager, unique_nick):
    """Factory to create IRCBot instances for testing."""

    def _create_bot(server: str = TEST_IRC_SERVER, port: int = TEST_PORT, use_tls: bool = False) -> IRCBot:
        server_config = {
            "nick": unique_nick,
            "port": port,
            "use_tls": use_tls,
            "verify_ssl": False,  # Disable SSL verification for self-signed test certs
            "channels": [],
            "random_nick": False,
        }
        bot = IRCBot(
            server=server,
            server_config=server_config,
            download_path=irc_bot_manager.config.get("default_download_path", "/tmp"),
            allowed_mimetypes=irc_bot_manager.config.get("allowed_mimetypes"),
            max_file_size=irc_bot_manager.config.get("max_file_size", 1073741824),
            bot_manager=irc_bot_manager,
        )
        return bot

    return _create_bot


@pytest.fixture
def irc_bot(irc_bot_factory):
    """Create a default IRCBot for testing."""
    return irc_bot_factory()
