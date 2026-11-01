import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import parse_qs, unquote, urlsplit

ATS_HOSTS = {
    "greenhouse": {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "job-boards.eu.greenhouse.io",
    },
    "ashby": {"jobs.ashbyhq.com"},
    "lever": {"jobs.lever.co", "jobs.eu.lever.co"},
    "workable": {"apply.workable.com", "jobs.workable.com"},
    "smartrecruiters": {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"},
}

# These are ATS-owned form APIs, not arbitrary upload destinations. They are
# usable only after the user authorizes a corresponding application-page host.
ATS_API_HOSTS = {
    "greenhouse": {"boards-api.greenhouse.io", "boards-api.eu.greenhouse.io"},
    "ashby": {"jobs.ashbyhq.com", "api.ashbyhq.com"},
    "lever": {"jobs.lever.co", "jobs.eu.lever.co", "api.lever.co", "api.eu.lever.co"},
    "workable": {"apply.workable.com"},
    "smartrecruiters": {"api.smartrecruiters.com", "jobs.smartrecruiters.com"},
}


def automation_provider(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return next((key for key, hosts in ATS_HOSTS.items() if host in hosts), "generic")


def verified_application_url(url: str, authorized_domains: list[str]) -> str:
    """Exact, explicitly approved HTTPS destination; never suffix/wildcard trust."""
    p = urlsplit(url)
    if (p.scheme != "https" or not p.hostname or p.username or p.password
            or p.port not in (None, 443) or p.hostname.lower() not in authorized_domains):
        raise ValueError("Разрешите точный HTTPS-домен страницы в настройках откликов: " + (p.hostname or "неизвестен"))
    return p.hostname.lower()


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


def is_submission_request(provider: str, url: str, method: str, post_data: str | None = None) -> bool:
    """Recognize final application writes before the durable approval fence.

    This supplements the browser's native non-GET navigation gate. Public form
    queries and uploads remain usable during preparation. Unknown custom XHR
    semantics cannot be inferred from a URL; those sites require manual review.
    No body values are logged or treated as candidate instructions.
    """
    if method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return False
    target = urlsplit(url)
    path = unquote(target.path).casefold().rstrip("/")
    query_params = parse_qs(target.query)
    operations = [str(value) for key in ("op", "operation", "operationName") for value in query_params.get(key, [])]
    if post_data and len(post_data) < 512000 and post_data.lstrip().startswith(("{", "[")):
        try:
            decoded = json.loads(post_data)
            for entry in decoded if isinstance(decoded, list) else [decoded]:
                if isinstance(entry, dict):
                    for key in ("operationName", "operation", "query"):
                        if isinstance(entry.get(key), str):
                            operations.append(entry[key])
        except (ValueError, TypeError):
            pass
    operation_text = " ".join(operations).casefold()
    # Hosted Ashby uses a named GraphQL operation; its documented API uses
    # /applicationForm.submit. Match operation names, never variables/answers.
    if re.search(
        r"(?:submit|send|create|complete)[_.-]?(?:job)?application(?:form)?|"
        r"application(?:form)?[_.-]?(?:submit|send|create|complete)|applytojob", operation_text
    ):
        return True
    if re.search(r"(?:^|[/._-])submit(?:application)?(?:$|[/._-])", path):
        return True
    # File attachment/draft document operations are preparation, not submission.
    if re.search(r"(?:^|[/._-])(?:uploads?|attachments?|files?|documents?|resumes?)(?:$|[/._-])", path):
        return False
    if re.search(r"(?:^|[/._-])apply(?:$|[/._-])", path):
        return True
    if provider == "greenhouse" and re.search(r"/(?:v\d+/boards/[^/]+/)?jobs/[^/]+$", path):
        return True
    if provider == "lever" and re.search(r"/v\d+/postings/[^/]+/[^/]+$", path):
        return True
    if provider == "ashby" and re.search(r"/applicationform\.(?:submit|create)$", path):
        return True
    # Workable and SmartRecruiters both create a candidate/application through
    # an endpoint with this resource name. This also protects common custom ATS.
    return bool(re.search(r"/(?:applications?|candidates?)(?:/[^/]+)?$", path))


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


