from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import ssl
from typing import Any

from .config import Settings


class SecurityConfigurationError(RuntimeError):
    pass


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def uvicorn_security_options(settings: Settings) -> dict[str, Any]:
    """Validate the listener boundary and return direct-Uvicorn TLS options."""
    if not settings.tls_enabled:
        if not is_loopback_host(settings.host):
            raise SecurityConfigurationError(
                "Cleartext HTTP/WebSocket is allowed only on a loopback listener"
            )
        return {}

    try:
        ipaddress.ip_address(settings.tls_server_ip.strip())
    except ValueError as exc:
        raise SecurityConfigurationError(
            "security.server_ip must be an IPv4 or IPv6 address"
        ) from exc

    certificate = Path(settings.tls_certificate_path).expanduser()
    private_key = Path(settings.tls_private_key_path).expanduser()
    if not certificate.is_file() or not os.access(certificate, os.R_OK):
        raise SecurityConfigurationError(
            f"TLS certificate is missing or unreadable: {certificate}"
        )
    if not private_key.is_file() or not os.access(private_key, os.R_OK):
        raise SecurityConfigurationError(
            f"TLS private key is missing or unreadable: {private_key}"
        )
    if private_key.stat().st_mode & 0o077:
        raise SecurityConfigurationError(
            f"TLS private key permissions must be 0600 or stricter: {private_key}"
        )
    if not settings.client_api_key:
        raise SecurityConfigurationError("Client API key is not configured")

    # Load the pair before opening the listener. This catches malformed files
    # and certificate/private-key mismatches without accepting any connection.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(str(certificate), str(private_key))
    except (OSError, ssl.SSLError) as exc:
        raise SecurityConfigurationError(f"Invalid TLS certificate/key pair: {exc}") from exc

    return {
        "ssl_certfile": str(certificate),
        "ssl_keyfile": str(private_key),
        "ssl_version": ssl.PROTOCOL_TLS_SERVER,
    }
