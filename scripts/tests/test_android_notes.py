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


class ScrollHelperTests(unittest.TestCase):
    VIEW = (0, 0, 1080, 2000)

    def test_parse_bounds(self):
        self.assertEqual((1, 2, 3, 4), notes.parse_bounds("[1,2][3,4]"))

    def test_fully_visible_requires_whole_rect_inside_viewport(self):
        self.assertTrue(notes.fully_visible((100, 1800, 900, 1900), self.VIEW))
        self.assertFalse(notes.fully_visible((100, 1900, 900, 2100), self.VIEW))
        self.assertFalse(notes.fully_visible((100, 2100, 900, 2200), self.VIEW))
        self.assertFalse(notes.fully_visible((100, -5, 900, 50), self.VIEW))
        self.assertFalse(notes.fully_visible((100, 100, 100, 200), self.VIEW))

    def test_swipe_points_stay_inside_viewport(self):
        sx, sy, ex, ey = notes.swipe_points(self.VIEW)
        for x, y in ((sx, sy), (ex, ey)):
            self.assertTrue(0 < x < 1080 and 0 < y < 2000)
        self.assertGreater(sy, ey)

    def _root(self, ime_top=None):
        import xml.etree.ElementTree as ET
        ime = '<node package="com.google.android.inputmethod.latin" bounds="[0,%s][1080,2364]"/>' % ime_top if ime_top else ""
        return ET.fromstring('<hierarchy><node package="%s" bounds="[0,0][1080,2364]"/>%s</hierarchy>' % (notes.PACKAGE, ime))

    def test_viewport_excludes_ime(self):
        self.assertEqual((0, 0, 1080, 2364), notes.app_viewport(self._root()))
        self.assertEqual((0, 0, 1080, 1500), notes.app_viewport(self._root(1500)))

    def _device(self, button_bounds_by_dump):
        import xml.etree.ElementTree as ET
        dev = notes.Device.__new__(notes.Device)
        dumps = iter(button_bounds_by_dump)
        dev.calls = []
        def fake_dump(name=None):
            b = next(dumps, button_bounds_by_dump[-1])
            return ET.fromstring('<hierarchy><node package="%s" bounds="[0,0][1080,2000]">'
                                 '<node package="%s" text="Add note" bounds="%s"/></node></hierarchy>' % (notes.PACKAGE, notes.PACKAGE, b))
        dev.dump = fake_dump
        dev.shell = lambda *a, **k: dev.calls.append(a)
        return dev

    def test_tap_scrolling_taps_immediately_when_visible(self):
        dev = self._device(["[100,1000][900,1100]"])
        dev.tap_scrolling("Add note")
        self.assertEqual([("input", "tap", "500", "1050")], dev.calls)

    def test_tap_scrolling_scrolls_then_taps(self):
        dev = self._device(["[100,2100][900,2200]", "[100,1000][900,1100]"])
        dev.tap_scrolling("Add note")
        self.assertEqual(["swipe", "tap"], [c[1] for c in dev.calls])

    def test_tap_scrolling_caps_scroll_attempts(self):
        dev = self._device(["[100,2100][900,2200]"])
        with self.assertRaises(RuntimeError):
            dev.tap_scrolling("Add note")
        self.assertEqual(notes.MAX_SCROLLS, len(dev.calls))
        self.assertTrue(all(c[1] == "swipe" for c in dev.calls))

    def test_tap_scrolling_never_taps_partially_visible_node(self):
        dev = self._device(["[100,1950][900,2050]"])
        with self.assertRaises(RuntimeError):
            dev.tap_scrolling("Add note")
        self.assertNotIn("tap", [c[1] for c in dev.calls])


class EvidenceTests(unittest.TestCase):
    def test_scenarios_pass_requires_every_result(self):
        good = {"draft_survives_process_death": True, "posts": 1, "submit_shows_note_card": True,
                "no_link_nodes_on_list": True, "no_link_nodes_on_detail": True, "forget_cancel_deletes": 0,
                "forget_confirm_deletes": 1, "413_message": True, "sentinel_body_in_ui": False}
        self.assertTrue(notes.scenarios_pass(good))
        self.assertFalse(notes.scenarios_pass({}))
        self.assertFalse(notes.scenarios_pass({**good, "posts": 2}))
        self.assertFalse(notes.scenarios_pass({**good, "sentinel_body_in_ui": True}))

    def test_write_evidence_persists_partial_results(self):
        import contextlib, io, tempfile
        results = {"ABORTED": "x"}
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()) as buf:
            notes.write_evidence(Path(d), results)
            self.assertTrue((Path(d) / "request-log.json").exists())
        self.assertIn("ABORTED", buf.getvalue())


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
