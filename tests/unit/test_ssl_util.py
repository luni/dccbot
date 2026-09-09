"""Tests for DCC SSL certificate utilities."""

from __future__ import annotations

import stat
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from dccbot.ssl_util import create_dcc_ssl_context, get_or_create_dcc_cert


def _generate_pair(tmp_path: Path, common_name: str = "test") -> tuple[Path, Path]:
    """Helper to create a self-signed cert/key pair for testing."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(common_name)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "test-cert.pem"
    key_path = tmp_path / "test-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.fixture
def empty_config() -> dict[str, Any]:
    """Return an empty configuration dict."""
    return {}


def test_get_or_create_dcc_cert_generates_self_signed(tmp_path: Path, empty_config: dict[str, Any]) -> None:
    """A self-signed certificate is generated when none is configured."""
    cert_file, key_file = get_or_create_dcc_cert(empty_config, cache_dir=tmp_path)

    cert_path = Path(cert_file)
    key_path = Path(key_file)
    assert cert_path.is_file()
    assert key_path.is_file()
    assert cert_path.parent == tmp_path
    assert key_path.parent == tmp_path

    # Key file should be readable/writable only by the owner.
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    # Certificate should parse and have the expected subject.
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    assert cert.not_valid_before_utc < cert.not_valid_after_utc
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == "dccbot"


def test_get_or_create_dcc_cert_uses_user_provided(tmp_path: Path) -> None:
    """Configured certificate paths are used when present and valid."""
    cert_path, key_path = _generate_pair(tmp_path)
    config = {"dcc_ssl_cert": str(cert_path), "dcc_ssl_key": str(key_path)}

    returned_cert, returned_key = get_or_create_dcc_cert(config, cache_dir=tmp_path)
    assert returned_cert == str(cert_path)
    assert returned_key == str(key_path)

    # No new cert should have been generated in the cache dir.
    assert not (tmp_path / "dcc-cert.pem").exists()


def test_get_or_create_dcc_cert_caches(tmp_path: Path, empty_config: dict[str, Any]) -> None:
    """Repeated calls return the same generated certificate paths."""
    first = get_or_create_dcc_cert(empty_config, cache_dir=tmp_path)
    second = get_or_create_dcc_cert(empty_config, cache_dir=tmp_path)
    assert first == second
    assert first[0] == str(tmp_path / "dcc-cert.pem")


def test_create_dcc_ssl_context_server(tmp_path: Path) -> None:
    """Server SSL context is created with cert loaded and verification disabled."""
    cert_path, key_path = _generate_pair(tmp_path)
    ctx = create_dcc_ssl_context(server=True, cert_path=str(cert_path), key_path=str(key_path))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_create_dcc_ssl_context_client(tmp_path: Path) -> None:
    """Client SSL context is created with cert loaded and verification disabled."""
    cert_path, key_path = _generate_pair(tmp_path)
    ctx = create_dcc_ssl_context(server=False, cert_path=str(cert_path), key_path=str(key_path))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_get_or_create_dcc_cert_falls_back_when_key_missing(tmp_path: Path) -> None:
    """If only a certificate is configured (no key), a self-signed cert is generated."""
    cert_path, _ = _generate_pair(tmp_path)
    config = {"dcc_ssl_cert": str(cert_path)}

    cert_file, key_file = get_or_create_dcc_cert(config, cache_dir=tmp_path)

    # Generated certificate, not the user one.
    assert cert_file == str(tmp_path / "dcc-cert.pem")
    assert key_file == str(tmp_path / "dcc-key.pem")


def test_get_or_create_dcc_cert_regenerates_when_cache_key_missing(tmp_path: Path) -> None:
    """A cached cert with a missing key triggers regeneration of the pair."""
    # Generate a cert/key pair in the cache dir, then delete only the key.
    _ = get_or_create_dcc_cert({}, cache_dir=tmp_path)
    (tmp_path / "dcc-key.pem").unlink()

    cert_file, key_file = get_or_create_dcc_cert({}, cache_dir=tmp_path)
    assert cert_file == str(tmp_path / "dcc-cert.pem")
    assert key_file == str(tmp_path / "dcc-key.pem")
    assert Path(cert_file).is_file()
    assert Path(key_file).is_file()


def test_create_dcc_ssl_context_single_file(tmp_path: Path) -> None:
    """A single PEM file containing both cert and key is accepted."""
    cert_path, key_path = _generate_pair(tmp_path)
    combined = tmp_path / "combined.pem"
    combined.write_bytes(cert_path.read_bytes() + key_path.read_bytes())

    ctx = create_dcc_ssl_context(server=False, cert_path=str(combined), key_path=str(combined))
    assert isinstance(ctx, ssl.SSLContext)


def test_create_dcc_ssl_context_mismatched_key_raises(tmp_path: Path) -> None:
    """A certificate paired with a mismatched private key raises an error."""
    cert_path, _ = _generate_pair(tmp_path)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_key_path = tmp_path / "other-key.pem"
    other_key_path.write_bytes(
        other_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    with pytest.raises(ssl.SSLError):
        create_dcc_ssl_context(server=False, cert_path=str(cert_path), key_path=str(other_key_path))


def test_get_or_create_dcc_cert_default_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The default cache dir is under the user's home directory."""
    monkeypatch.setattr("dccbot.ssl_util._default_cert_cache_dir", lambda: tmp_path)
    cert_file, key_file = get_or_create_dcc_cert({})
    assert cert_file.endswith("/dcc-cert.pem")
    assert key_file.endswith("/dcc-key.pem")
    assert Path(cert_file).parent == tmp_path
