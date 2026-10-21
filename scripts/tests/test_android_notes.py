"""Pure-helper and mock-server checks for the notes acceptance runner; no device access."""

import hashlib
import importlib.util
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "android_notes.py"
spec = importlib.util.spec_from_file_location("android_notes", RUNNER)
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)


class HelperTests(unittest.TestCase):
    def test_body_digest_has_length_and_hash_only(self):
        digest = notes.body_digest(b"secret note")
        self.assertEqual({"len", "sha256"}, set(digest))
        self.assertEqual(hashlib.sha256(b"secret note").hexdigest(), digest["sha256"])

    def test_adb_text_encodes_spaces_and_rejects_shell_characters(self):
        self.assertEqual("a%sb", notes.adb_text("a b"))
        for bad in ("a;b", "a$(x)", "a'b", "a&b", "a\nb"):
            with self.assertRaises(ValueError):
                notes.adb_text(bad)

    def test_adb_text_accepts_url_and_token_punctuation(self):
        self.assertEqual("http://127.0.0.1:18766", notes.adb_text("http://127.0.0.1:18766"))
        self.assertEqual("notes-local-token", notes.adb_text(notes.TOKEN))
        self.assertEqual("a_b=c@d+e%sf", notes.adb_text("a_b=c@d+e f"))
        notes.adb_text("http://127.0.0.1:%d" % 18766)

    def test_adb_text_still_rejects_percent_and_shell_metacharacters(self):
        for bad in ("100%", "a|b", "a`b`", "a\\b", 'a"b', "a#b", "a*b", "a~b", "a<b", "a>b", "a(b", "a!b", "a?b", "a\tb"):
            with self.assertRaises(ValueError, msg=bad):
                notes.adb_text(bad)

    def test_note_fixture_text_is_typeable(self):
        notes.adb_text(notes.NOTE_TEXT)
        notes.adb_text(notes.NOTE_TITLE)
        self.assertGreaterEqual(len(notes.NOTE_TEXT), 100)

    def test_counter_value_parses_separators(self):
        self.assertEqual(1234, notes.counter_value(["x", "1,234 / 60,000"]))
        self.assertEqual(0, notes.counter_value(["0 / 60,000"]))
        self.assertIsNone(notes.counter_value(["Note text", "12 / 500"]))

    def test_link_labels(self):
        self.assertEqual(["Check source"], notes.link_labels_present(["Your note", "Check source"]))
        self.assertEqual([], notes.link_labels_present(["Your note: Lecture"]))

    def test_count_requests(self):
        log = [{"method": "DELETE", "path": "/a"}, {"method": "GET", "path": "/a"}, {"method": "DELETE", "path": "/a"}]
        self.assertEqual(2, notes.count_requests(log, "DELETE", "/a"))


class MockServerTests(unittest.TestCase):
    def setUp(self):
        notes.STATE.update(log=[], text_status=200, done=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), notes.make_handler())
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, method, path, body=None, token=notes.TOKEN):
        request = urllib.request.Request(self.base + path, data=body, method=method,
                                         headers={"Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_log_records_digest_never_the_body(self):
        body = json.dumps({"title": "T", "text": "PRIVATE-NOTE-TEXT"}).encode()
        status, _ = self.call("POST", "/api/v1/ingest/text", body)
        self.assertEqual(200, status)
        entry = notes.STATE["log"][-1]
        self.assertEqual(("POST", "/api/v1/ingest/text", 200, len(body)), (entry["method"], entry["path"], entry["status"], entry["len"]))
        self.assertEqual(hashlib.sha256(body).hexdigest(), entry["sha256"])
        self.assertNotIn("PRIVATE-NOTE-TEXT", json.dumps(notes.STATE["log"]))

    def test_error_mode_returns_status_and_unauthorized_is_logged(self):
        self.call("GET", "/__ctl/text_status?code=413")
        self.assertEqual(413, self.call("POST", "/api/v1/ingest/text", b"{}")[0])
        self.assertEqual(401, self.call("GET", "/api/v1/feed/today", token="wrong")[0])
        self.assertEqual([413, 401], [e["status"] for e in notes.STATE["log"]])

    def test_forget_delete_is_204_and_feed_appears_after_submit(self):
        self.assertEqual(b"[]", self.call("GET", "/api/v1/feed/today")[1])
        self.call("POST", "/api/v1/ingest/text", b"{}")
        self.assertEqual("note", json.loads(self.call("GET", "/api/v1/feed/today")[1])[0]["source_kind"])
        self.assertEqual(204, self.call("DELETE", "/api/v1/notes/note-doc")[0])


if __name__ == "__main__":
    unittest.main()
