from __future__ import annotations

import io
import socket
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from search_governor.fetcher import fetch_one, fetch_with_browser_fallback
from search_governor.models import Candidate
from search_governor.url_safety import (
    MAX_REDIRECTS,
    SafeHTTPRedirectHandler,
    UnsafeUrlError,
    UrlResolutionError,
    validate_external_http_url,
)


def dns_answer(address: str) -> list[tuple]:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sockaddr = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]


class UrlSafetyTests(unittest.TestCase):
    def test_private_local_and_malformed_targets_are_rejected(self) -> None:
        rejected = [
            "http://localhost/",
            "http://127.0.0.1/",
            "http://127.10.20.30/",
            "http://0.0.0.0/",
            "http://10.0.0.1/",
            "http://172.16.0.1/",
            "http://172.31.255.255/",
            "http://192.168.1.1/",
            "http://169.254.169.254/",
            "https://203.0.113.10/example",
            "http://[::1]/",
            "http://[fc00::1]/",
            "http://[fe80::1]/",
            "http://user:password@example.com/",
            "file:///etc/passwd",
            "https://example.com:99999/",
            "https://example.com/\nheader: injected",
        ]
        for url in rejected:
            with self.subTest(url=url), self.assertRaises(UnsafeUrlError):
                validate_external_http_url(url)

    @patch("search_governor.url_safety.socket.getaddrinfo", return_value=dns_answer("93.184.216.34"))
    def test_public_http_urls_are_allowed(self, getaddrinfo: Mock) -> None:
        validate_external_http_url("https://example.com/article")
        validate_external_http_url("https://93.184.216.34/example")
        self.assertEqual(2, getaddrinfo.call_count)

    @patch("search_governor.url_safety.socket.getaddrinfo", return_value=dns_answer("127.0.0.1"))
    def test_domain_resolving_to_loopback_is_rejected(self, _getaddrinfo: Mock) -> None:
        with self.assertRaises(UnsafeUrlError):
            validate_external_http_url("https://example.test/")

    @patch(
        "search_governor.url_safety.socket.getaddrinfo",
        return_value=dns_answer("93.184.216.34") + dns_answer("10.0.0.1"),
    )
    def test_mixed_public_and_private_dns_is_rejected(self, _getaddrinfo: Mock) -> None:
        with self.assertRaises(UnsafeUrlError):
            validate_external_http_url("https://mixed.example.test/")

    @patch("search_governor.url_safety.socket.getaddrinfo", side_effect=socket.gaierror("not found"))
    def test_dns_failure_is_a_resolution_error(self, _getaddrinfo: Mock) -> None:
        with self.assertRaises(UrlResolutionError):
            validate_external_http_url("https://missing.example.test/")

    @patch("search_governor.url_safety.socket.getaddrinfo", return_value=dns_answer("93.184.216.34"))
    def test_public_redirect_is_allowed(self, _getaddrinfo: Mock) -> None:
        handler = SafeHTTPRedirectHandler()
        redirected = handler.redirect_request(
            urllib.request.Request("https://public.example/start"),
            io.BytesIO(),
            302,
            "Found",
            {},
            "https://other.example/article",
        )
        self.assertEqual("https://other.example/article", redirected.full_url)

    def test_redirect_to_loopback_or_metadata_is_rejected(self) -> None:
        handler = SafeHTTPRedirectHandler()
        request = urllib.request.Request("https://public.example/start")
        for target in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/"):
            with self.subTest(target=target), self.assertRaises(UnsafeUrlError):
                handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, target)

    @patch("search_governor.url_safety.socket.getaddrinfo", return_value=dns_answer("93.184.216.34"))
    def test_redirect_limit_is_enforced(self, _getaddrinfo: Mock) -> None:
        handler = SafeHTTPRedirectHandler()

        class Parent:
            @staticmethod
            def open(request, timeout=None):  # type: ignore[no-untyped-def]
                request.timeout = timeout
                return request

        handler.parent = Parent()
        request = urllib.request.Request("https://public.example/start")
        request.timeout = None
        for index in range(MAX_REDIRECTS):
            request = handler.http_error_302(
                request,
                io.BytesIO(b""),
                302,
                "Found",
                {"location": f"https://public.example/redirect-{index}"},
            )
        with self.assertRaises(urllib.error.HTTPError):
            handler.http_error_302(
                request,
                io.BytesIO(b""),
                302,
                "Found",
                {"location": "https://public.example/too-many"},
            )

    @patch("search_governor.fetcher.subprocess.run")
    def test_unsafe_candidate_is_blocked_without_browser_fallback(self, subprocess_run: Mock) -> None:
        candidate = Candidate("unsafe", "Unsafe", "http://127.0.0.1/", "snippet", "demo", 1)
        cfg = {
            "provider_capabilities": {"demo": {"external_fetch": True}},
            "browser_fallback_enabled": True,
            "browser_fallback_error_kinds": ["blocked", "rate_limited", "empty"],
        }
        result = fetch_one(candidate, cfg)
        self.assertEqual("blocked", result.fetch_status)
        self.assertEqual("unsafe_url", result.extra["fetch_error_kind"])
        subprocess_run.assert_not_called()

    @patch("search_governor.fetcher.subprocess.run")
    def test_browser_fallback_revalidates_initial_url(self, subprocess_run: Mock) -> None:
        candidate = Candidate("unsafe-browser", "Unsafe", "http://10.0.0.1/", "", "demo", 1)
        result = fetch_with_browser_fallback(candidate, {"browser_fallback_script": __file__}, "blocked", "blocked")
        self.assertEqual("blocked", result.fetch_status)
        self.assertEqual("unsafe_url", result.extra["fetch_error_kind"])
        subprocess_run.assert_not_called()

    @patch("search_governor.fetcher.validate_external_http_url")
    @patch("search_governor.fetcher.subprocess.run")
    def test_browser_fallback_rejects_unsafe_final_url(self, subprocess_run: Mock, validate_url: Mock) -> None:
        validate_url.side_effect = [None, UnsafeUrlError("unsafe target: private or local network address")]
        subprocess_run.return_value.returncode = 0
        subprocess_run.return_value.stdout = (
            '{"ok":true,"status":"ok","final_url":"http://127.0.0.1/","cleaned_text":"private body"}'
        )
        candidate = Candidate("unsafe-final", "Unsafe", "https://public.example/", "", "demo", 1)
        result = fetch_with_browser_fallback(candidate, {"browser_fallback_script": __file__}, "blocked", "blocked")
        self.assertEqual("blocked", result.fetch_status)
        self.assertEqual("unsafe_url", result.extra["fetch_error_kind"])
        self.assertIsNone(result.fetched_content)


if __name__ == "__main__":
    unittest.main()
