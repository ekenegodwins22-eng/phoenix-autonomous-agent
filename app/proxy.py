"""Configured browser-proxy helpers. Public proxy discovery is intentionally unsupported."""
from urllib.parse import urlparse
from app.config import settings

SUPPORTED_SCHEMES = {"http", "https", "socks5", "socks5h"}


def validate_proxy_server(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in SUPPORTED_SCHEMES or not parsed.hostname:
        raise ValueError("Proxy must use http, https, socks5, or socks5h and include a host")
    if parsed.username or parsed.password:
        raise ValueError("Put proxy credentials in protected server configuration, not the task request")
    return value.rstrip('/')


def configured_proxy() -> dict | None:
    server = validate_proxy_server(settings.browser_proxy_server)
    if not server:
        return None
    parsed = urlparse(server)
    return {
        "server": server,
        "username": settings.browser_proxy_username or None,
        "password": settings.browser_proxy_password or None,
        "identifier": f"{parsed.scheme}://{parsed.hostname}:{parsed.port or {'http': 80, 'https': 443, 'socks5': 1080, 'socks5h': 1080}.get(parsed.scheme, '')}",
    }


def public_status() -> dict:
    proxy = configured_proxy()
    return {
        "mode": "configured" if proxy else "direct",
        "configured": bool(proxy),
        "identifier": proxy["identifier"] if proxy else None,
        "credentials_configured": bool(proxy and proxy.get("username")),
    }
