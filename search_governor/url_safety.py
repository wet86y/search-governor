from __future__ import annotations

import ipaddress
import socket
import urllib.request
from urllib.parse import urljoin, urlsplit


MAX_REDIRECTS = 5
UNSAFE_URL_ERROR_KIND = "unsafe_url"
UNSAFE_NETWORK_MESSAGE = "unsafe target: private or local network address"
UNSAFE_URL_MESSAGE = "unsafe target: invalid external URL"


class UnsafeUrlError(ValueError):
    """Raised when an external fetch target violates the network safety policy."""

    def __init__(self, public_message: str = UNSAFE_URL_MESSAGE):
        super().__init__(public_message)
        self.public_message = public_message


class UrlResolutionError(OSError):
    """Raised when a syntactically valid external URL cannot be resolved."""


def _has_control_chars(value: str) -> bool:
    return any(ord(char) <= 32 or ord(char) == 127 for char in value)


def _is_disallowed_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def validate_external_http_url(url: str) -> None:
    """Reject URLs that are malformed or resolve to non-public network targets."""

    if not isinstance(url, str) or not url or _has_control_chars(url):
        raise UnsafeUrlError()
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError() from exc

    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise UnsafeUrlError()
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError()
    if _has_control_chars(hostname) or "%" in hostname:
        raise UnsafeUrlError()

    normalized_hostname = hostname.rstrip(".").lower()
    if normalized_hostname in {"localhost", "localhost.localdomain"} or normalized_hostname.endswith(".localhost"):
        raise UnsafeUrlError(UNSAFE_NETWORK_MESSAGE)

    try:
        literal_address = ipaddress.ip_address(normalized_hostname)
    except ValueError:
        literal_address = None
    if literal_address is not None and _is_disallowed_ip(literal_address):
        raise UnsafeUrlError(UNSAFE_NETWORK_MESSAGE)

    service_port = port if port is not None else (443 if parsed.scheme.lower() == "https" else 80)
    try:
        answers = socket.getaddrinfo(normalized_hostname, service_port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as exc:
        raise UrlResolutionError("external target DNS resolution failed") from exc

    resolved: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for answer in answers:
        try:
            resolved.add(ipaddress.ip_address(answer[4][0]))
        except (IndexError, TypeError, ValueError) as exc:
            raise UrlResolutionError("external target DNS resolution returned no usable address") from exc
    if not resolved:
        raise UrlResolutionError("external target DNS resolution returned no usable address")
    if any(_is_disallowed_ip(address) for address in resolved):
        raise UnsafeUrlError(UNSAFE_NETWORK_MESSAGE)


class SafeHTTPRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalidate every redirect target before urllib follows it."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        absolute_url = urljoin(req.full_url, newurl)
        validate_external_http_url(absolute_url)
        return super().redirect_request(req, fp, code, msg, headers, absolute_url)


def build_safe_http_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(SafeHTTPRedirectHandler())
