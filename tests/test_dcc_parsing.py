"""Unit tests for DCC parsing/validation helpers."""

from dccbot.dcc_parsing import is_valid_filename, parse_dcc_accept, parse_dcc_send


def test_parse_dcc_accept_valid():
    """Test parsing valid DCC ACCEPT payload."""
    assert parse_dcc_accept('ACCEPT "file.mkv" 5000 1234') == (5000, 1234)


def test_parse_dcc_accept_invalid():
    """Test parsing invalid DCC ACCEPT payload."""
    assert parse_dcc_accept("ACCEPT invalid") is None
    assert parse_dcc_accept('ACCEPT "file.mkv" 100 0') is None


def test_parse_dcc_send_valid_ipv4_num():
    """Test parsing valid DCC SEND payload with numeric IPv4."""
    parsed = parse_dcc_send('SEND "file.mkv" 2130706433 5000 1024')
    assert parsed is not None
    assert parsed.filename == "file.mkv"
    assert parsed.peer_address == "127.0.0.1"
    assert parsed.peer_port == 5000
    assert parsed.size == 1024


def test_parse_dcc_send_rejects_invalid():
    """Test parser rejects malformed DCC SEND payload."""
    assert parse_dcc_send('SEND "file.mkv" x.x.x.x 5000 1024') is None
    assert parse_dcc_send('SEND "file.mkv" 127.0.0.1 -1 1024') is None
    assert parse_dcc_send('SEND "file.mkv" 127.0.0.1 5000 0') is None


def test_is_valid_filename():
    """Test filename path validation helper."""
    assert is_valid_filename("/tmp/downloads", "file.mkv") is True
    assert is_valid_filename("/tmp/downloads", "../file.mkv") is False
    assert is_valid_filename("/tmp/downloads", "dir/file.mkv") is False
