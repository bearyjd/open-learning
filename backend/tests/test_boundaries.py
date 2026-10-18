import io
import logging
import multiprocessing
import socket
import time
from types import SimpleNamespace

import httpx
import pytest
from conftest import PASSAGE
from pydantic import ValidationError

from open_learning import fetching
from open_learning.ai import InsightBatch, ModelClient, ModelError, verify_quote
from open_learning.config import Settings
from open_learning.fetching import FetchError, PinnedConnection, public_destination


def test_config_fails_closed():
    with pytest.raises(ValidationError):
        Settings(api_token="short")
    with pytest.raises(ValidationError):
        Settings(api_token="a" * 32)


@pytest.mark.parametrize(
    "quote", [" " * 30, "invented quote not present in the article", "\n" * 40]
)
def test_grounding_rejects_fabricated_or_empty_quotes(quote):
    with pytest.raises(ModelError):
        verify_quote(quote, PASSAGE)


def test_grounding_allows_whitespace_normalization():
    verify_quote(PASSAGE.strip().replace(" ", "\n"), PASSAGE)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.1.1.1",
        "::1",
        "::ffff:127.0.0.1",
        "0.0.0.0",
        "fc00::1",
        "224.0.0.1",
        "ff02::1",
    ],
)
def test_ssrf_dns_rejected(monkeypatch, address):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, 1, 6, "", (address, 80))]
    )
    with pytest.raises(FetchError):
        public_destination("public.example", 80)


def test_mixed_public_private_dns_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, 1, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, 1, 6, "", ("127.0.0.1", 80)),
        ],
    )
    with pytest.raises(FetchError):
        public_destination("public.example", 80)


def test_connection_uses_validated_ip_without_second_dns(monkeypatch):
    calls = []

    class FakeSocket:
        def settimeout(self, timeout):
            pass

        def connect(self, address):
            calls.append(address)

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *a, **kw: FakeSocket())
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: pytest.fail("Second DNS lookup"))
    connection = PinnedConnection(
        "article.example", 80, (socket.AF_INET, "93.184.216.34"), False, 1
    )
    connection.connect()
    assert calls == [("93.184.216.34", 80)]


class FakeResponse:
    status = 200

    def __init__(self, data=b"A" * 200, headers=None):
        self.data = io.BytesIO(data)
        self.headers = headers or {"Content-Type": "text/plain"}
        self.fp = SimpleNamespace(
            raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=lambda value: None))
        )

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, size):
        return self.data.read(size)


def stub_connection(monkeypatch, response):
    monkeypatch.setattr(
        fetching, "public_destination", lambda *a: (socket.AF_INET, "93.184.216.34")
    )

    class Connection:
        def __init__(self, *a):
            pass

        def request(self, *a, **kw):
            pass

        def getresponse(self):
            return response

        def close(self):
            pass

    monkeypatch.setattr(fetching, "PinnedConnection", Connection)


def test_size_bound_without_content_length(monkeypatch, settings):
    settings.fetch_max_bytes = 1024
    stub_connection(monkeypatch, FakeResponse(data=b"A" * 1025))
    with pytest.raises(FetchError, match="size limit"):
        fetching._fetch_document("https://example.com", settings)


def test_redirect_to_private_address_rejected(monkeypatch, settings):
    response = FakeResponse(headers={"Location": "http://127.0.0.1/secret"})
    response.status = 302
    stub_connection(monkeypatch, response)
    with pytest.raises(FetchError, match="Public internet"):
        fetching._fetch_document("https://example.com", settings)


@pytest.mark.parametrize("stage", ["dns", "headers"])
def test_wallclock_bound_covers_slow_dns_and_headers(monkeypatch, settings, stage):
    context = multiprocessing.get_context("fork")
    monkeypatch.setattr(multiprocessing, "get_context", lambda *a: context)
    settings.fetch_timeout_seconds = 0.1

    def slow(*a):
        time.sleep(5)

    if stage == "dns":
        monkeypatch.setattr(fetching, "public_destination", slow)
    else:
        stub_connection(monkeypatch, FakeResponse())
        monkeypatch.setattr(fetching.PinnedConnection, "getresponse", slow)
    start = time.monotonic()
    with pytest.raises(FetchError, match="timed out"):
        fetching.fetch_document("https://example.com", settings)
    assert time.monotonic() - start < 2


def test_real_model_client_validates_structure_and_quotes(monkeypatch, settings):
    original = httpx.Client
    response = {
        "choices": [
            {
                "message": {
                    "content": '{"insights": [{"title":"Idea","idea":"Recall","explanation":"Practice","why_it_matters":"Memory","evidence":"fabricated evidence which is absent","chunk_index":0,"tags":[]}]}'
                }
            }
        ]
    }
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))
        ),
    )
    with pytest.raises(ModelError, match="absent"):
        ModelClient(settings).insights([PASSAGE])
    response["choices"][0]["message"]["content"] = '{"insights": []}'
    with pytest.raises(ModelError, match="invalid structured"):
        ModelClient(settings).insights([PASSAGE])


def test_model_http_failure_is_explicit(monkeypatch, settings):
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(transport=httpx.MockTransport(lambda request: httpx.Response(503))),
    )
    with pytest.raises(ModelError, match="unavailable"):
        ModelClient(settings).complete(InsightBatch, "test", {})


def test_extraction_child_does_not_log_reading_url(monkeypatch, settings, caplog):
    sensitive_url = "https://example.com/private-reading?topic=personal"

    def failing_extractor(url, config):
        logging.getLogger("trafilatura.core").warning("discarding data: %s", url)
        raise FetchError("No readable article found")

    monkeypatch.setattr(fetching, "_fetch_document", failing_extractor)
    previous_level = logging.root.manager.disable
    messages = []
    sender = SimpleNamespace(send=messages.append, close=lambda: None)
    try:
        fetching._fetch_in_child(sender, sensitive_url, settings)
    finally:
        logging.disable(previous_level)
    assert sensitive_url not in caplog.text
    assert messages == [(False, "No readable article found")]
