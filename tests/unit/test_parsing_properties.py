"""Property-based tests for DCC parsing/validation helpers."""

import ipaddress

import irc.client
import pytest
from hypothesis import assume, given, settings, strategies as st

from dccbot.dcc_parsing import DccAcceptPayload, DccSendPayload, is_valid_filename, parse_dcc_accept, parse_dcc_send

BASE_PATH = "/downloads"

SAFE_FILENAME = st.from_regex(r"[A-Za-z0-9_.~][A-Za-z0-9_.~\-]{0,40}", fullmatch=True)


@given(payload=st.text())
@settings(max_examples=1000)
def test_parse_dcc_send_never_raises(payload: str):
    """Arbitrary payloads either parse to a payload object or return None."""
    result = parse_dcc_send(payload)
    assert result is None or isinstance(result, DccSendPayload)


@given(payload=st.text())
@settings(max_examples=1000)
def test_parse_dcc_accept_never_raises(payload: str):
    """Arbitrary payloads either parse to a payload object or return None."""
    result = parse_dcc_accept(payload)
    assert result is None or isinstance(result, DccAcceptPayload)


@given(payload=st.text())
def test_parse_dcc_send_result_invariants(payload: str):
    """Parsed send payloads always have in-range fields and a valid IP."""
    result = parse_dcc_send(payload)
    if result is None:
        return
    assert result.filename
    assert 0 <= result.peer_port <= 65535
    assert result.size >= 1
    assert result.token is None or result.token >= 0
    ipaddress.ip_address(result.peer_address)


@given(payload=st.text())
def test_parse_dcc_accept_result_invariants(payload: str):
    """Parsed accept payloads always have in-range fields."""
    result = parse_dcc_accept(payload)
    if result is None:
        return
    assert 0 <= result.port <= 65535
    assert result.position >= 0
    assert result.token is None or result.token >= 0


@given(
    filename=SAFE_FILENAME,
    ip=st.integers(0, 2**32 - 1),
    port=st.integers(0, 65535),
    size=st.integers(1, 2**40),
)
def test_parse_dcc_send_roundtrip(filename: str, ip: int, port: int, size: int):
    """A well-formed numeric-IPv4 send payload round-trips all fields."""
    parsed = parse_dcc_send(f"SEND {filename} {ip} {port} {size}")
    assert parsed is not None
    assert parsed.filename == filename
    assert parsed.peer_address == irc.client.ip_numstr_to_quad(str(ip))
    assert parsed.peer_port == port
    assert parsed.size == size
    assert parsed.token is None


@given(
    port=st.integers(0, 65535),
    position=st.integers(0, 2**40),
)
def test_parse_dcc_accept_roundtrip(port: int, position: int):
    """A well-formed accept payload round-trips all fields."""
    parsed = parse_dcc_accept(f"ACCEPT name {port} {position}")
    assert parsed is not None
    assert parsed.port == port
    assert parsed.position == position
    assert parsed.token is None


@given(
    filename=SAFE_FILENAME,
    ip=st.integers(0, 2**32 - 1),
    port=st.integers(0, 65535),
    size=st.integers(1, 2**40),
    token=st.integers(0, 2**32 - 1),
)
def test_parse_dcc_send_roundtrip_with_token(filename: str, ip: int, port: int, size: int, token: int):
    """A passive send payload with token round-trips all fields."""
    parsed = parse_dcc_send(f"SEND {filename} {ip} {port} {size} {token}")
    assert parsed is not None
    assert parsed.token == token


@given(payload=st.text())
def test_parse_dcc_send_deterministic(payload: str):
    """Parsing the same payload twice yields equal results."""
    assert parse_dcc_send(payload) == parse_dcc_send(payload)


@given(
    prefix=st.text(max_size=20),
    bad=st.sampled_from("/\\:*?\"<>|"),
    suffix=st.text(max_size=20),
)
def test_filename_with_forbidden_char_rejected(prefix: str, bad: str, suffix: str):
    """Filenames containing path separators or shell-unsafe chars are rejected."""
    assert not is_valid_filename(BASE_PATH, prefix + bad + suffix)


@given(prefix=st.text(max_size=20), suffix=st.text(max_size=20))
def test_filename_with_null_byte_rejected(prefix: str, suffix: str):
    """Filenames containing NUL bytes are rejected before filesystem use."""
    assert not is_valid_filename(BASE_PATH, prefix + "\x00" + suffix)


@pytest.mark.parametrize("filename", ["", ".", ".."])
def test_special_filenames_rejected(filename: str):
    """Empty, current-dir, and parent-dir filenames are rejected."""
    assert not is_valid_filename(BASE_PATH, filename)


@given(filename=SAFE_FILENAME)
def test_simple_filenames_accepted(filename: str):
    """Filenames without forbidden chars resolve inside the base path."""
    assume(filename not in (".", ".."))
    assert is_valid_filename(BASE_PATH, filename)
