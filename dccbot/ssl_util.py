"""Certificate and SSL context helpers for Secure DCC (SDCC)."""

from __future__ import annotations

import logging
import os
import ssl
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

logger = logging.getLogger(__name__)

DEFAULT_CERT_FILE = "dcc-cert.pem"
DEFAULT_KEY_FILE = "dcc-key.pem"


def _default_cert_cache_dir() -> Path:
    """Return the default directory for generated DCC certificates."""
    return Path.home() / ".local" / "share" / "dccbot"


def _generate_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    """Generate a 2048-bit RSA self-signed certificate and write it to disk."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "dccbot")])
    cert = (
        x509
        .CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("dccbot")]), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    # Set restrictive permissions on the private key.
    os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)


def get_or_create_dcc_cert(config: dict[str, Any], cache_dir: Path | None = None) -> tuple[str, str]:
    """Return paths to a certificate and private key for DCC SSL.

    If the user configured ``dcc_ssl_cert`` and ``dcc_ssl_key`` and the files
    exist, those paths are returned. Otherwise a 2048-bit RSA self-signed
    certificate is generated in ``cache_dir`` (default ``~/.local/share/dccbot``).

    Args:
        config: Bot configuration dictionary.
        cache_dir: Optional directory to store a generated certificate.

    Returns:
        Tuple of (certificate file path, key file path).

    """
    cert_file = config.get("dcc_ssl_cert")
    key_file = config.get("dcc_ssl_key")

    if cert_file and key_file:
        cert_path = Path(cert_file)
        key_path = Path(key_file)
        if cert_path.is_file() and key_path.is_file():
            return str(cert_path), str(key_path)
        logger.warning("Configured dcc_ssl_cert/dcc_ssl_key not found, generating self-signed certificate")

    cache = cache_dir or _default_cert_cache_dir()
    cert_path = cache / DEFAULT_CERT_FILE
    key_path = cache / DEFAULT_KEY_FILE

    if cert_path.is_file() and key_path.is_file():
        return str(cert_path), str(key_path)

    logger.info("Generating self-signed DCC certificate in %s", cache)
    _generate_self_signed_cert(cert_path, key_path)
    return str(cert_path), str(key_path)


def create_dcc_ssl_context(server: bool, cert_path: str, key_path: str) -> ssl.SSLContext:
    """Create an SSL context for a DCC transfer.

    Args:
        server: ``True`` for a passive/listening DCC endpoint (TLS server),
            ``False`` for an active/connecting DCC endpoint (TLS client).
        cert_path: Path to the PEM certificate file.
        key_path: Path to the PEM private key file.

    Returns:
        An ``ssl.SSLContext`` configured for DCC/SDCC.

    """
    purpose = ssl.Purpose.CLIENT_AUTH if server else ssl.Purpose.SERVER_AUTH
    ctx = ssl.create_default_context(purpose)
    ctx.load_cert_chain(cert_path, key_path)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
