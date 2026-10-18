"""Exercise HTTPResponse framing and real HTML extraction through a local server."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import PASSAGE

from open_learning import fetching


@pytest.mark.parametrize("missing_bytes", [0, 100], ids=["complete", "incomplete"])
def test_content_length_response_extracts_complete_article(monkeypatch, settings, missing_bytes):
    html = (Path(__file__).parent / "fixtures" / "article.html").read_bytes()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers["Accept-Encoding"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html) + missing_bytes))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection_type = fetching.PinnedConnection
    # Route only the network boundary to the controlled server; HTTP framing and
    # Trafilatura remain real. The public destination policy has separate tests.
    monkeypatch.setattr(fetching, "public_destination", lambda *args: (socket.AF_INET, "127.0.0.1"))
    monkeypatch.setattr(
        fetching,
        "PinnedConnection",
        lambda host, port, destination, tls, timeout: connection_type(
            host, server.server_port, destination, False, timeout
        ),
    )
    try:
        if missing_bytes:
            with pytest.raises(fetching.FetchError, match="Incomplete source response"):
                fetching._fetch_document("http://article.example/read?chapter=1", settings)
            return
        article = fetching._fetch_document("http://article.example/read?chapter=1", settings)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert requests == [("/read?chapter=1", "identity")]
    assert article.title == "Retrieval practice and memory"
    assert PASSAGE.strip() in article.text
    assert "Subscribe for updates" not in article.text
    assert not article.truncated
