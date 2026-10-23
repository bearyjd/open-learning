# SPDX-License-Identifier: GPL-3.0-only
"""Feed parsing and download boundaries, with every network operation mocked."""

import io
import multiprocessing
import socket
import time
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from open_learning import feed_fetching as feeds

BASE = "https://public.example/feeds/rss.xml"
RSS = b"""<rss version="2.0"><channel><title>Learning</title><item>
<title>Recall</title><link>/articles/recall#part</link>
<pubDate>Fri, 23 Oct 2026 12:00:00 GMT</pubDate>
<description><![CDATA[<p>Hello <b>world</b>.</p><script>secret()</script>]]></description>
</item></channel></rss>"""


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    def resolve(host, port, **kwargs):
        address = "127.0.0.1" if host == "private.example" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def test_rss_resolves_links_strips_html_and_has_immutable_results():
    result = feeds.parse_feed(RSS, BASE)
    assert result.title == "Learning"
    assert len(result.items) == 1
    item = result.items[0]
    assert item.url == "https://public.example/articles/recall"
    assert item.title == "Recall"
    assert item.published_at == datetime(2026, 10, 23, 12, tzinfo=UTC)
    assert item.snippet == "Hello world."
    with pytest.raises(FrozenInstanceError):
        item.title = "changed"
    assert isinstance(result.items, tuple)


def test_atom_alternate_links_dates_and_xhtml_content():
    content = b"""<feed xmlns="http://www.w3.org/2005/Atom">
    <title>Atom learning</title><entry><title>Practice</title>
    <link rel="self" href="https://public.example/api/one"/>
    <link rel="alternate" href="../one"/>
    <updated>2026-10-23T12:30:00Z</updated>
    <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">
    <p>Try <b>recall</b>.</p></div></content></entry></feed>"""
    result = feeds.parse_feed(content, BASE)
    assert result.title == "Atom learning"
    assert result.items[0].url == "https://public.example/one"
    assert result.items[0].published_at == datetime(2026, 10, 23, 12, 30, tzinfo=UTC)
    assert result.items[0].snippet == "Try recall."


@pytest.mark.parametrize("content", [b"", b"<rss>", b"<html/>", b"<rss version='1.0'/>"])
def test_rejects_malformed_or_unsupported_feed_with_fixed_error(content):
    with pytest.raises(feeds.FeedError, match="^feed_invalid$"):
        feeds.parse_feed(content, BASE)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
@pytest.mark.parametrize(
    "declarations",
    [
        '<!ENTITY x SYSTEM "file:///etc/passwd">',
        '<!ENTITY a "123"><!ENTITY x "&a;&a;&a;&a;">',
        "",
    ],
)
def test_rejects_all_dtds_including_encoded_entity_expansion(encoding, declarations):
    xml = "<!DOCTYPE rss [" + declarations + ']><rss version="2.0"><channel/> </rss>'
    with pytest.raises(feeds.FeedError, match="^feed_invalid$"):
        feeds.parse_feed(xml.encode(encoding), BASE)


def test_input_size_is_bounded_before_parsing():
    with pytest.raises(feeds.FeedError, match="^feed_too_large$"):
        feeds.parse_feed(b" " * (1_048_576 + 1), BASE)


def test_deep_xml_is_rejected_before_recursive_text_serialization():
    content = b'<rss version="2.0"><channel><title>' + b"<b>" * 1100
    content += b"deep" + b"</b>" * 1100 + b"</title></channel></rss>"
    with pytest.raises(feeds.FeedError, match="^feed_invalid$"):
        feeds.parse_feed(content, BASE)


def test_items_are_deduplicated_capped_and_plain_text_is_bounded():
    item = "<item><title>Title</title><link>/article/{}</link><description>{}</description></item>"
    content = '<rss version="2.0"><channel><title>Feed</title>'
    content += item.format(0, "x" * 600) * 2
    content += "".join(item.format(n, "snippet") for n in range(1, 80))
    content += "</channel></rss>"
    result = feeds.parse_feed(content.encode(), BASE)
    assert len(result.items) == 50
    assert len(result.items[0].snippet) == 500
    assert len({item.url for item in result.items}) == 50


def test_skips_invalid_private_and_non_http_item_links():
    links = [
        "http://127.0.0.1/",
        "http://private.example/",
        "file:///etc/passwd",
        "https://user:pass@public.example/",
        "https://public.example:8080/",
        "/safe",
    ]
    content = (
        '<rss version="2.0"><channel>'
        + "".join(f"<item><link>{link}</link><pubDate>invalid</pubDate></item>" for link in links)
        + "</channel></rss>"
    )
    result = feeds.parse_feed(content.encode(), BASE)
    assert [item.url for item in result.items] == ["https://public.example/safe"]
    assert result.items[0].published_at is None


def test_atom_honors_xml_base_and_rss_guid_permalink():
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom" xml:base="../">
    <entry xml:base="articles/"><title>A</title><link href="one"/></entry></feed>"""
    assert feeds.parse_feed(atom, BASE).items[0].url == "https://public.example/articles/one"
    rss = b"""<rss version="2.0"><channel><item><guid isPermaLink="true">/one</guid>
    </item><item><guid isPermaLink="false">identifier</guid></item></channel></rss>"""
    assert len(feeds.parse_feed(rss, BASE).items) == 1


def test_item_url_utf8_byte_bound_and_entry_inspection_bound():
    content = '<rss version="2.0"><channel><item><link>https://public.example/'
    content += "é" * 1100 + "</link></item></channel></rss>"
    assert feeds.parse_feed(content.encode(), BASE).items == ()
    content = '<rss version="2.0"><channel>' + "<item><link>file:///private</link></item>" * 100
    content += "<item><link>/after-bound</link></item></channel></rss>"
    assert feeds.parse_feed(content.encode(), BASE).items == ()


def test_atom_xhtml_removes_scripts_and_preserves_paragraph_spaces():
    content = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><link href="/one"/>
    <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>First</p><p>Second</p>
    <script>secret()</script></div></content></entry></feed>"""
    assert feeds.parse_feed(content, BASE).items[0].snippet == "First Second"


def test_invalid_xml_base_has_fixed_error():
    with pytest.raises(feeds.FeedError, match="^feed_invalid$"):
        feeds.parse_feed(b'<feed xmlns="http://www.w3.org/2005/Atom" xml:base="http://["/>', BASE)


class Response:
    def __init__(self, body=RSS, *, status=200, headers=None):
        self.body = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}
        self.fp = SimpleNamespace(
            raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=lambda value: None))
        )

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, size):
        return self.body.read(size)

    def isclosed(self):
        return False


def connections(monkeypatch, *responses):
    requests, destinations, closed = [], [], []
    pending = iter(responses)

    class Connection:
        def __init__(self, host, port, destination, tls, timeout):
            destinations.append((host, port, destination, tls))

        def request(self, method, path, headers):
            requests.append((method, path, headers))

        def getresponse(self):
            return next(pending)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(feeds, "PinnedConnection", Connection)
    return requests, destinations, closed


def test_fetch_pins_dns_and_sends_conditional_headers(monkeypatch, settings):
    requests, destinations, closed = connections(
        monkeypatch,
        Response(
            headers={"ETag": '"version-one"', "Last-Modified": "Fri, 23 Oct 2026 12:00:00 GMT"}
        ),
    )
    result = feeds._fetch_feed(BASE, settings, etag='"old"', last_modified="yesterday")
    assert result.feed.title == "Learning"
    assert result.etag == '"version-one"'
    assert result.last_modified == "Fri, 23 Oct 2026 12:00:00 GMT"
    assert requests[0][:2] == ("GET", "/feeds/rss.xml")
    assert requests[0][2]["If-None-Match"] == '"old"'
    assert requests[0][2]["If-Modified-Since"] == "yesterday"
    assert requests[0][2]["Accept-Encoding"] == "identity"
    assert destinations == [("public.example", 443, (socket.AF_INET, "93.184.216.34"), True)]
    assert closed == [True]


def test_unicode_request_paths_are_ascii_escaped(monkeypatch, settings):
    requests, _, _ = connections(monkeypatch, Response())
    feeds._fetch_feed("https://public.example/é?q=é", settings)
    assert requests[0][1] == "/%C3%A9?q=%C3%A9"


def test_not_modified_has_no_feed_and_preserves_validators(monkeypatch, settings):
    connections(monkeypatch, Response(status=304))
    result = feeds._fetch_feed(BASE, settings, etag='"old"', last_modified="yesterday")
    assert result == feeds.FeedResponse(None, '"old"', "yesterday")


@pytest.mark.parametrize("url", ["http://127.0.0.1/a", "http://private.example/a", "file:///x"])
def test_revalidates_redirect_destination(monkeypatch, settings, url):
    _, destinations, closed = connections(
        monkeypatch, Response(status=302, headers={"Location": url})
    )
    with pytest.raises(feeds.FeedError, match="^feed_url_invalid$"):
        feeds._fetch_feed(BASE, settings)
    assert len(destinations) == len(closed) == 1


def test_relative_redirect_changes_item_base_and_rechecks_dns(monkeypatch, settings):
    _, destinations, _ = connections(
        monkeypatch,
        Response(status=301, headers={"Location": "https://other.example/feed.xml"}),
        Response(
            body=b'<rss version="2.0"><channel><item><link>/one</link></item></channel></rss>'
        ),
    )
    result = feeds._fetch_feed(BASE, settings)
    assert result.feed.items[0].url == "https://other.example/one"
    assert [item[0] for item in destinations] == ["public.example", "other.example"]


@pytest.mark.parametrize(
    "body,headers,code",
    [
        (b"x" * 1_048_577, {}, "feed_too_large"),
        (RSS, {"Content-Length": "1048577"}, "feed_too_large"),
        (RSS, {"Content-Length": "invalid"}, "feed_too_large"),
        (RSS, {"Content-Length": "9" * 5000}, "feed_too_large"),
        (RSS, {"Content-Length": str(len(RSS) + 10)}, "feed_incomplete"),
        (RSS, {"Content-Encoding": "gzip"}, "feed_encoding"),
    ],
)
def test_download_boundaries(monkeypatch, settings, body, headers, code):
    connections(monkeypatch, Response(body, headers=headers))
    with pytest.raises(feeds.FeedError, match=f"^{code}$"):
        feeds._fetch_feed(BASE, settings)


def test_bad_http_status_never_exposes_remote_data(monkeypatch, settings):
    connections(monkeypatch, Response(b"secret token", status=500))
    with pytest.raises(feeds.FeedError, match="^feed_http_error$"):
        feeds._fetch_feed(BASE, settings)


def test_cache_headers_cannot_inject_requests_and_remote_validators_are_bounded(
    monkeypatch, settings
):
    requests, _, _ = connections(
        monkeypatch, Response(headers={"ETag": "x" * 3000, "Last-Modified": "x\r\ny"})
    )
    result = feeds._fetch_feed(BASE, settings, etag="x\r\ny", last_modified="x" * 3000)
    assert "If-None-Match" not in requests[0][2]
    assert "If-Modified-Since" not in requests[0][2]
    assert result.etag is None and result.last_modified is None


@pytest.mark.parametrize("stage", ["dns", "headers"])
def test_process_deadline_includes_dns_and_headers(monkeypatch, settings, stage):
    context = multiprocessing.get_context("fork")
    monkeypatch.setattr(feeds.multiprocessing, "get_context", lambda *args: context)
    settings.fetch_timeout_seconds = 0.1

    def slow(*args):
        time.sleep(5)

    if stage == "dns":
        monkeypatch.setattr(feeds, "public_destination", slow)
    else:
        connections(monkeypatch, Response())
        monkeypatch.setattr(feeds.PinnedConnection, "getresponse", slow)
    start = time.monotonic()
    with pytest.raises(feeds.FeedError, match="^feed_timeout$"):
        feeds.fetch_feed(BASE, settings)
    assert time.monotonic() - start < 2


def test_child_errors_are_fixed_and_do_not_leak_exception_text(monkeypatch, settings):
    messages = []
    sender = SimpleNamespace(send=messages.append, close=lambda: None)

    def fail(*args, **kwargs):
        raise ValueError("secret remote error")

    monkeypatch.setattr(feeds, "_fetch_feed", fail)
    monkeypatch.setattr(feeds.logging, "disable", lambda level: None)
    feeds._fetch_in_child(sender, BASE, settings, None, None)
    assert messages == [(False, "feed_fetch_failed")]
