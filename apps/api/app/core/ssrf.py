"""SSRF protection for user-supplied hosts/URLs (PRD MD-9, §18.3)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import settings
from app.core.errors import Problem

INTERNAL_HOSTNAMES = {
    "postgres",
    "redis",
    "qdrant",
    "minio",
    "api",
    "webhook",
    "worker",
    "web",
    "caddy",
    "mailpit",
}
METADATA_IPS = {"169.254.169.254", "fd00:ec2::254", "100.100.100.200"}


def _is_blocked_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or ip in METADATA_IPS
    )


def resolve_public_host(host: str, allow_private: bool | None = None) -> list[str]:
    """Resolve host and return its IPs; raise 400 if any resolves to a blocked range."""
    allow_private = settings.allow_private_endpoints if allow_private is None else allow_private
    host = host.strip().strip("[]")
    if not host:
        raise Problem(400, "Host is required")
    if host.lower() in INTERNAL_HOSTNAMES and not allow_private:
        raise Problem(400, "Host is not allowed")
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise Problem(400, f"Could not resolve host '{host}'") from e
    ips = sorted({str(info[4][0]) for info in infos})
    if not ips:
        raise Problem(400, f"Could not resolve host '{host}'")
    if not allow_private:
        blocked = [ip for ip in ips if _is_blocked_ip(ip)]
        if blocked:
            raise Problem(400, "Host resolves to a private or reserved address, which is not allowed")
    return ips


def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise Problem(400, "URL must be http(s) with a hostname")
    resolve_public_host(parsed.hostname)
    return url
