# SPDX-License-Identifier: GPL-3.0-only
"""Bounded RSS/Atom parsing and DNS-pinned downloads with fixed failure codes."""

import http.client
import logging
import multiprocessing
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from itertools import islice
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from .config import Settings
from .fetching import FetchError, PinnedConnection, public_destination, validate_url

MAX_BYTES = 1_048_576
MAX_ITEMS = 50
MAX_EXAMINED_ITEMS = 100
ATOM = "{http://www.w3.org/2005/Atom}"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
ERROR_CODES = frozenset(
    {
        "feed_invalid",
        "feed_url_invalid",
        "feed_too_large",
        "feed_timeout",
        "feed_http_error",
        "feed_redirect_error",
        "feed_encoding",
        "feed_connection_error",
        "feed_incomplete",
        "feed_fetch_failed",
    }
)


class FeedError(ValueError):
    def __init__(self, code: str):
        super().__init__(code if code in ERROR_CODES else "feed_fetch_failed")


@dataclass(frozen=True)
class FeedEntry:
    url: str
    title: str
    published_at: datetime | None
    snippet: str


@dataclass(frozen=True)
class ParsedFeed:
    title: str
    items: tuple[FeedEntry, ...]


@dataclass(frozen=True)
class FeedResponse:
    feed: ParsedFeed | None
    etag: str | None = None
    last_modified: str | None = None


class _SafeTreeBuilder(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = 0

    def start(self, tag, attrs):
        self.depth += 1
        if self.depth > 64:
            raise FeedError("feed_invalid")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def doctype(self, name, pubid, system):
        # Expat invokes this before reading the internal subset, for every XML encoding.
        # Forbid all DTDs, including harmless ones, so no entities can be declared.
        raise FeedError("feed_invalid")


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.rsplit(":", 1)[-1]
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "div", "br", "li", "section", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        tag = tag.rsplit(":", 1)[-1]
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "li", "section", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    parser = _PlainText()
    # Serialization keeps XHTML block boundaries and script/style tags visible to the stripper.
    raw = ET.tostring(node, encoding="unicode", method="html") if len(node) else node.text or ""
    parser.feed(raw)
    parser.close()
    return " ".join("".join(parser.parts).split())[:500]


def _date(value: str) -> datetime | None:
    try:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(value)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        return None


def _checked_url(url: str) -> tuple[str, str, int, tuple[int, str]]:
    try:
        if len(url.encode("utf-8")) > 2048:
            raise FeedError("feed_url_invalid")
        url = validate_url(url)
        parsed = urlsplit(url)
        host = parsed.hostname.encode("idna").decode("ascii")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return url, host, port, public_destination(host, port)
    except (FetchError, ValueError, UnicodeError, OSError):
        raise FeedError("feed_url_invalid") from None


def _join(base: str, target: str) -> str:
    try:
        return urljoin(base, target)
    except ValueError:
        raise FeedError("feed_invalid") from None


def _entry(node: ET.Element, base: str, atom: bool) -> FeedEntry | None:
    base = _join(base, node.get(XML_BASE, ""))
    if atom:
        prefix = ATOM
        link = next(
            (
                link
                for link in node.findall(ATOM + "link")
                if link.get("rel", "alternate") == "alternate" and link.get("href")
            ),
            None,
        )
        if link is None:
            return None
        base = _join(base, link.get(XML_BASE, ""))
        target = link.get("href")
        published = node.findtext(ATOM + "published") or node.findtext(ATOM + "updated") or ""
        snippet = node.find(ATOM + "summary")
        if snippet is None:
            snippet = node.find(ATOM + "content")
    else:
        prefix = ""
        target = node.findtext("link")
        if not target:
            guid = node.find("guid")
            if guid is not None and guid.get("isPermaLink", "true").lower() == "true":
                target = guid.text
        published = node.findtext("pubDate") or ""
        snippet = node.find("description")
        if snippet is None:
            snippet = node.find("{http://purl.org/rss/1.0/modules/content/}encoded")
    if not target:
        return None
    try:
        url = _checked_url(urljoin(base, target.strip()))[0]
    except (FeedError, ValueError):
        return None
    return FeedEntry(
        url,
        _text(node.find(prefix + "title")) or url[:500],
        _date(published.strip()),
        _text(snippet),
    )


def parse_feed(content: bytes, base_url: str) -> ParsedFeed:
    if len(content) > MAX_BYTES:
        raise FeedError("feed_too_large")
    try:
        parser = ET.XMLParser(target=_SafeTreeBuilder())
        root = ET.fromstring(content, parser=parser)
    except (ET.ParseError, ValueError, LookupError):
        raise FeedError("feed_invalid") from None
    base = _join(base_url, root.get(XML_BASE, ""))
    atom = root.tag == ATOM + "feed"
    if atom:
        container, prefix, item_name = root, ATOM, ATOM + "entry"
    elif root.tag == "rss" and root.get("version") == "2.0":
        container, prefix, item_name = root.find("channel"), "", "item"
    else:
        raise FeedError("feed_invalid")
    if container is None:
        raise FeedError("feed_invalid")
    if container is not root:
        base = _join(base, container.get(XML_BASE, ""))
    items, seen = [], set()
    for node in islice((node for node in container if node.tag == item_name), MAX_EXAMINED_ITEMS):
        try:
            item = _entry(node, base, atom)
        except FeedError:
            item = None
        if item is not None and item.url not in seen:
            seen.add(item.url)
            items.append(item)
            if len(items) == MAX_ITEMS:
                break
    return ParsedFeed(_text(container.find(prefix + "title")), tuple(items))


def _validator(value: str | None) -> str | None:
    if value and len(value) <= 2048 and all(32 <= ord(char) < 127 for char in value):
        return value
    return None


def _fetch_feed(url: str, settings: Settings, *, etag=None, last_modified=None) -> FeedResponse:
    deadline = time.monotonic() + settings.fetch_timeout_seconds
    etag, last_modified = _validator(etag), _validator(last_modified)
    for redirect in range(4):
        url, host, port, destination = _checked_url(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FeedError("feed_timeout")
        parsed = urlsplit(url)
        connection = PinnedConnection(host, port, destination, parsed.scheme == "https", remaining)
        try:
            headers = {
                "User-Agent": "OpenLearning/0.1",
                "Accept-Encoding": "identity",
                "Accept": "application/atom+xml,application/rss+xml,application/xml,text/xml",
            }
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified
            path = urlunsplit(
                (
                    "",
                    "",
                    quote(parsed.path or "/", safe="/:@!$&'()*+,;=-._~%"),
                    quote(parsed.query, safe="/?:@!$&'()*+,;=-._~%"),
                    "",
                )
            )
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            response_etag = _validator(response.getheader("ETag"))
            response_modified = _validator(response.getheader("Last-Modified"))
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if redirect == 3 or not location:
                    raise FeedError("feed_redirect_error")
                try:
                    url = urljoin(url, location)
                except ValueError:
                    raise FeedError("feed_url_invalid") from None
                continue
            if response.status == 304:
                return FeedResponse(None, response_etag or etag, response_modified or last_modified)
            if response.status != 200:
                raise FeedError("feed_http_error")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise FeedError("feed_encoding")
            length = response.getheader("Content-Length")
            if length and (
                len(length) > 16
                or not length.isascii()
                or not length.isdigit()
                or int(length) > MAX_BYTES
            ):
                raise FeedError("feed_too_large")
            content = bytearray()
            while not response.isclosed():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FeedError("feed_timeout")
                response.fp.raw._sock.settimeout(remaining)
                block = response.read1(min(65_536, MAX_BYTES + 1 - len(content)))
                if not block:
                    break
                content.extend(block)
                if len(content) > MAX_BYTES:
                    raise FeedError("feed_too_large")
            if length and len(content) != int(length):
                raise FeedError("feed_incomplete")
            return FeedResponse(parse_feed(bytes(content), url), response_etag, response_modified)
        except (OSError, http.client.HTTPException):
            raise FeedError("feed_connection_error") from None
        finally:
            connection.close()
    raise FeedError("feed_redirect_error")


def _fetch_in_child(sender, url: str, settings: Settings, etag, last_modified):
    logging.disable(logging.CRITICAL)
    try:
        sender.send((True, _fetch_feed(url, settings, etag=etag, last_modified=last_modified)))
    except Exception as error:
        sender.send((False, str(error) if isinstance(error, FeedError) else "feed_fetch_failed"))
    finally:
        sender.close()


def fetch_feed(url: str, settings: Settings, *, etag=None, last_modified=None) -> FeedResponse:
    # DNS, headers, parsing, and entry DNS checks all share a wall-clock deadline.
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_fetch_in_child, args=(sender, url, settings, etag, last_modified), daemon=True
    )
    process.start()
    sender.close()
    try:
        if not receiver.poll(settings.fetch_timeout_seconds):
            raise FeedError("feed_timeout")
        success, result = receiver.recv()
        if not success:
            raise FeedError(result)
        return result
    except EOFError:
        raise FeedError("feed_fetch_failed") from None
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)
