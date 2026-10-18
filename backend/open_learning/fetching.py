"""Bounded HTTP fetches. DNS is checked once and connections use that exact IP."""

import http.client
import ipaddress
import logging
import multiprocessing
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from .config import Settings


class FetchError(ValueError):
    pass


def is_public(address: str) -> bool:
    parsed = ipaddress.ip_address(address)
    return parsed.is_global and not parsed.is_multicast and not parsed.is_reserved


def validate_url(url: str) -> str:
    if len(url) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in url) or "\\" in url:
        raise FetchError("Invalid URL")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise FetchError("Only HTTP and HTTPS URLs are supported")
        if parsed.username or parsed.password or port not in {None, 80, 443}:
            raise FetchError("Credentials and nonstandard ports are forbidden")
        host = parsed.hostname.encode("idna").decode("ascii")
        try:
            if not is_public(host):
                raise FetchError("Public internet destinations only")
        except ValueError as error:
            if isinstance(error, FetchError):
                raise
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
    except (ValueError, UnicodeError) as error:
        raise FetchError(str(error)) from error


def public_destination(host: str, port: int) -> tuple[int, str]:
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise FetchError("DNS resolution failed") from error
    if not addresses or any(not is_public(item[4][0]) for item in addresses):
        raise FetchError("Public internet destinations only")
    return addresses[0][0], addresses[0][4][0]


class PinnedConnection(http.client.HTTPConnection):
    def __init__(
        self, host: str, port: int, destination: tuple[int, str], tls: bool, timeout: float
    ):
        super().__init__(host, port, timeout=timeout)
        self.destination = destination
        self.tls = tls

    def connect(self):
        family, address = self.destination
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect((address, self.port))
            if self.tls:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
            self.sock = sock
        except BaseException:
            sock.close()
            raise


@dataclass
class FetchedDocument:
    url: str
    title: str
    text: str
    truncated: bool = False


def _fetch_document(url: str, settings: Settings) -> FetchedDocument:
    deadline = time.monotonic() + settings.fetch_timeout_seconds
    for redirect in range(4):
        url = validate_url(url)
        parsed = urlsplit(url)
        host = parsed.hostname.encode("idna").decode("ascii")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        destination = public_destination(host, port)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError("Fetch timed out")
        connection = PinnedConnection(host, port, destination, parsed.scheme == "https", remaining)
        try:
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            connection.request(
                "GET",
                path,
                headers={
                    "User-Agent": "OpenLearning/0.1",
                    "Accept": "text/html,text/plain",
                    "Accept-Encoding": "identity",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if redirect == 3 or not location:
                    raise FetchError("Too many redirects or missing redirect destination")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise FetchError(f"Source returned HTTP {response.status}")
            media_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if media_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise FetchError("Source must be HTML or plain text")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise FetchError("Compressed source responses are unsupported")
            length = response.getheader("Content-Length")
            if length and (not length.isdigit() or int(length) > settings.fetch_max_bytes):
                raise FetchError("Source exceeds download size limit")
            content = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FetchError("Fetch timed out")
                # HTTPResponse may detach its socket from the connection after reading headers.
                response.fp.raw._sock.settimeout(remaining)
                block = response.read1(min(65_536, settings.fetch_max_bytes + 1 - len(content)))
                if not block:
                    break
                content.extend(block)
                if len(content) > settings.fetch_max_bytes:
                    raise FetchError("Source exceeds download size limit")
            raw = bytes(content).decode("utf-8", errors="replace")
            if media_type == "text/plain":
                title, text = host, raw
            else:
                import trafilatura

                extracted = trafilatura.bare_extraction(raw, url=url, favor_precision=True)
                if extracted is None:
                    raise FetchError("No readable article found")
                title, text = extracted.title or host, extracted.text or ""
            text = text.strip()
            truncated = len(text) > settings.max_document_chars
            text = text[: settings.max_document_chars]
            if len(text) < 100:
                raise FetchError("Source has too little readable text")
            return FetchedDocument(url, title[:500], text, truncated)
        except (OSError, http.client.HTTPException) as error:
            raise FetchError("Source connection failed") from error
        finally:
            connection.close()
    raise FetchError("Too many redirects")


def _fetch_in_child(sender, url: str, settings: Settings):
    # Extraction libraries can log source URLs on parse failures. This isolated child emits none.
    logging.disable(logging.CRITICAL)
    try:
        sender.send((True, _fetch_document(url, settings)))
    except Exception as error:
        message = str(error) if isinstance(error, FetchError) else "Source extraction failed"
        sender.send((False, message))
    finally:
        sender.close()


def fetch_document(url: str, settings: Settings) -> FetchedDocument:
    # A separate process bounds libc DNS resolution, slow HTTP headers, and HTML parsing too.
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_fetch_in_child, args=(sender, url, settings), daemon=True)
    process.start()
    sender.close()
    try:
        if not receiver.poll(settings.fetch_timeout_seconds):
            raise FetchError("Fetch timed out")
        success, result = receiver.recv()
        if not success:
            raise FetchError(result)
        return result
    except EOFError as error:
        raise FetchError("Source extraction failed") from error
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
