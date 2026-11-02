"""Certificate and SSL context helpers for Secure DCC (SDCC)."""

from __future__ import annotations

import logging
import os
import ssl
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, padding, rsa
from cryptography.x509.oid import SignatureAlgorithmOID

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
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
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
    elif cert_file or key_file:
        logger.warning("Only one of dcc_ssl_cert/dcc_ssl_key is configured; both are required. Generating self-signed certificate")

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


def create_unverified_tls_context() -> ssl.SSLContext:
    """Create a client TLS context with certificate verification disabled.

    Returns:
        An ``ssl.SSLContext`` that accepts any certificate.

    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def is_self_signed_cert(cert: x509.Certificate) -> bool:
    """Return True when the certificate is self-issued and its self-signature verifies."""
    if cert.issuer != cert.subject:
        return False
    public_key = cert.public_key()
    signature_hash = cert.signature_hash_algorithm
    try:
        if isinstance(public_key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
            public_key.verify(cert.signature, cert.tbs_certificate_bytes)
        elif signature_hash is None:
            logger.warning("Certificate signature algorithm %s has no hash", cert.signature_algorithm_oid)
            return False
        elif isinstance(public_key, rsa.RSAPublicKey):
            if cert.signature_algorithm_oid == SignatureAlgorithmOID.RSASSA_PSS:
                public_key.verify(
                    cert.signature,
                    cert.tbs_certificate_bytes,
                    padding.PSS(mgf=padding.MGF1(signature_hash), salt_length=padding.PSS.MAX_LENGTH),
                    signature_hash,
                )
            else:
                public_key.verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), signature_hash)
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(cert.signature, cert.tbs_certificate_bytes, ec.ECDSA(signature_hash))
        elif isinstance(public_key, dsa.DSAPublicKey):
            public_key.verify(cert.signature, cert.tbs_certificate_bytes, signature_hash)
        else:
            logger.warning("Unsupported public key type %s on certificate", type(public_key).__name__)
            return False
    except InvalidSignature:
        return False
    except Exception:
        logger.warning("Could not verify certificate self-signature", exc_info=True)
        return False
    return True


def verify_peer_self_signed(ssl_object: ssl.SSLObject | ssl.SSLSocket | None, server: str) -> None:
    """Ensure the TLS peer presented a valid self-signed certificate.

    The certificate must be self-issued, carry a valid self-signature, and
    be inside its validity window. The hostname is intentionally not
    compared: IRC networks served by a self-signed certificate frequently
    use a node name (e.g. ``demigod.nl.eu.coreirc.net``) that differs from
    the round-robin address the client connected to.

    Args:
        ssl_object: The transport's SSL object, from
            ``transport.get_extra_info("ssl_object")``.
        server: The server address, used in error messages.

    Raises:
        ssl.SSLCertVerificationError: When no certificate was presented, it
            is not self-signed, its self-signature does not verify, or it is
            outside its validity window.

    """
    der = ssl_object.getpeercert(binary_form=True) if ssl_object is not None else None
    if not der:
        raise ssl.SSLCertVerificationError(f"peer {server} did not provide a certificate")
    try:
        cert = x509.load_der_x509_certificate(der)
    except ValueError as e:
        raise ssl.SSLCertVerificationError(f"could not parse certificate from {server}") from e
    if not is_self_signed_cert(cert):
        raise ssl.SSLCertVerificationError(f"certificate from {server} is not self-signed")
    now = datetime.now(UTC)
    if now < cert.not_valid_before_utc:
        raise ssl.SSLCertVerificationError(f"certificate from {server} is not yet valid")
    if now > cert.not_valid_after_utc:
        raise ssl.SSLCertVerificationError(f"certificate from {server} is expired")
