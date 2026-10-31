import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

ATS_HOSTS = {
    "greenhouse": {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "job-boards.eu.greenhouse.io",
    },
    "ashby": {"jobs.ashbyhq.com"},
    "lever": {"jobs.lever.co", "jobs.eu.lever.co"},
}


def validate_application_url(url: str, provider: str) -> None:
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or p.hostname not in ATS_HOSTS.get(provider, set())
        or p.username
        or p.password
        or p.port not in (None, 443)
    ):
        raise ValueError("Application URL must be a canonical HTTPS ATS URL")


async def public_url(url: str) -> bool:
    p = urlsplit(url)
    if p.scheme not in ("https", "http") or not p.hostname or p.port not in (None, 80, 443):
        return False
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            p.hostname, p.port or 443, type=socket.SOCK_STREAM
        )
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (OSError, ValueError):
        return False


