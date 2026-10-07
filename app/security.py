"""Network safety helpers for untrusted RSS links."""
import ipaddress
import os
import socket
from urllib.parse import urlparse

MAX_ARTICLE_BYTES = int(os.environ.get("MAX_ARTICLE_BYTES", "2000000"))
DEFAULT_ALLOWED_HOSTS = {
    "techcrunch.com", "wired.com", "arstechnica.com", "theverge.com", "engadget.com",
    "bbc.co.uk", "bbc.com", "theguardian.com",
}


def _allowed_hosts():
    configured = os.environ.get("ALLOWED_ARTICLE_HOSTS", "")
    return {h.strip().lower().lstrip(".") for h in configured.split(",") if h.strip()} or DEFAULT_ALLOWED_HOSTS


def _host_matches(host, allowed):
    return any(host == item or host.endswith("." + item) for item in allowed)


def validate_public_url(url: str, *, allowlist=True) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use http or https and include a host")
    host = parsed.hostname.lower().rstrip(".")
    if allowlist and not _host_matches(host, _allowed_hosts()):
        raise ValueError(f"host is not allowlisted: {host}")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except (OSError, ValueError) as exc:
        raise ValueError("host cannot be resolved") from exc
    for address in {info[4][0] for info in infos}:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("private or non-global destination blocked")
    return url
