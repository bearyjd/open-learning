"""Pure-helper and mock-server checks for the notes acceptance runner; no device access."""

import hashlib
import importlib.util
import json
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "android_notes.py"
sys.path.insert(0, str(RUNNER.parent))
spec = importlib.util.spec_from_file_location("android_notes", RUNNER)
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)
import android_notes_forget as forget


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
        self.assertEqual(3 * notes.MAX_SCROLLS, len(dev.calls))
        self.assertTrue(all(c[1] == "swipe" for c in dev.calls))

    def test_tap_scrolling_never_taps_partially_visible_node(self):
        dev = self._device(["[100,1950][900,2050]"])
        with self.assertRaises(RuntimeError):
            dev.tap_scrolling("Add note")
        self.assertNotIn("tap", [c[1] for c in dev.calls])


class SubmitHashTests(unittest.TestCase):
    def test_expected_body_is_compact_gson_shape(self):
        raw = notes.expected_submit_bodies()["label_omitted"]
        self.assertEqual({"title": notes.NOTE_TITLE, "text": notes.NOTE_TEXT}, json.loads(raw))
        self.assertTrue(raw.startswith(b'{"title":"Acceptance note","text":"'))

    def test_matching_digest_reports_variant(self):
        raw = notes.expected_submit_bodies()["label_omitted"]
        r = notes.check_submit_digest(notes.body_digest(raw))
        self.assertTrue(r["submit_hash_match"])
        self.assertEqual("label_omitted", r["submit_hash_variant"])

    def test_text_first_key_order_observed_on_device_matches(self):
        raw = notes.expected_submit_bodies()["label_omitted_text_first"]
        self.assertTrue(raw.startswith(b'{"text":'))
        self.assertEqual("label_omitted_text_first", notes.check_submit_digest(notes.body_digest(raw))["submit_hash_variant"])

    def test_mismatch_is_flagged_and_blocks_pass(self):
        r = notes.check_submit_digest({"len": 5, "sha256": "0" * 64})
        self.assertFalse(r["submit_hash_match"])
        good = {"draft_survives_process_death": True, "posts": 1, "submit_shows_note_card": True,
                "no_link_nodes_on_list": True, "no_link_nodes_on_detail": True, "forget_cancel_deletes": 0,
                "forget_confirm_deletes": 1, "413_message": True, "sentinel_body_in_ui": False,
                "429_message": True, "429_posts": 2, "429_same_body_hash": True, "429_completed": True,
                "429_sentinel_in_ui": False, "list_rows_shown": True, "list_forget_deletes": 1, "list_other_deletes": 0,
                "list_other_row_remains": True, "list_forgotten_row_gone": True,
                "offline_waiting_count": 1, "offline_deletes": 0, "offline_row_hidden": True, "online_deletes": 1, "online_waiting_gone": True}
        self.assertFalse(notes.scenarios_pass({**good, **r}))
        self.assertTrue(notes.scenarios_pass({**good, "submit_hash_match": True}))


class ReverseSwipeTests(unittest.TestCase):
    def test_reverse_swipe_mirrors_forward_inside_viewport(self):
        vp = (0, 100, 1080, 2300)
        sx, sy, ex, ey = notes.swipe_points(vp)
        self.assertEqual((sx, ey, ex, sy), notes.swipe_points_down(vp))
        self.assertLess(notes.swipe_points_down(vp)[1], notes.swipe_points_down(vp)[3])


class EvidenceTests(unittest.TestCase):
    def test_scenarios_pass_requires_every_result(self):
        good = {"draft_survives_process_death": True, "posts": 1, "submit_shows_note_card": True,
                "no_link_nodes_on_list": True, "no_link_nodes_on_detail": True, "forget_cancel_deletes": 0,
                "forget_confirm_deletes": 1, "413_message": True, "sentinel_body_in_ui": False,
                "429_message": True, "429_posts": 2, "429_same_body_hash": True, "429_completed": True,
                "429_sentinel_in_ui": False, "list_rows_shown": True, "list_forget_deletes": 1, "list_other_deletes": 0,
                "list_other_row_remains": True, "list_forgotten_row_gone": True,
                "offline_waiting_count": 1, "offline_deletes": 0, "offline_row_hidden": True, "online_deletes": 1, "online_waiting_gone": True}
        self.assertTrue(notes.scenarios_pass(good))
        for key, bad in (("429_message", False), ("429_posts", 1), ("429_posts", 3), ("429_same_body_hash", False),
                         ("429_completed", False), ("429_sentinel_in_ui", True), ("list_forget_deletes", 0),
                         ("list_other_deletes", 1), ("list_other_row_remains", False), ("list_forgotten_row_gone", False),
                         ("offline_deletes", 1), ("online_deletes", 2), ("online_waiting_gone", False)):
            self.assertFalse(notes.scenarios_pass({**good, key: bad}), key)
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
        notes.STATE.update(log=[], text_status=200, offline=False, done=False, rate_limit_once=False, retried=False, list_deleted=set(), dropped=[])
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
        self.assertEqual(204, self.call("DELETE", "/api/v1/notes/5eed0000-0000-4000-8000-00000000000c")[0])

    def test_rate_limit_once_answers_429_with_retry_after_then_202(self):
        self.call("GET", "/__ctl/rate_limit_once")
        body = b'{"text":"x"}'
        request = urllib.request.Request(self.base + "/api/v1/ingest/text", data=body, method="POST",
                                         headers={"Authorization": "Bearer " + notes.TOKEN})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(429, caught.exception.code)
        self.assertEqual("1", caught.exception.headers["Retry-After"])
        self.assertEqual(202, self.call("POST", "/api/v1/ingest/text", body)[0])
        self.assertEqual(202, self.call("POST", "/api/v1/ingest/text", body)[0])
        self.assertEqual([429, 202, 202], [e["status"] for e in notes.STATE["log"]])
        self.assertEqual(1, len({e["sha256"] for e in notes.STATE["log"]}))

    def test_rate_limit_scenario_summary(self):
        log = [{"method": "POST", "path": "/api/v1/ingest/text", "status": s, "len": 5, "sha256": "h"} for s in (200, 429, 202)]
        summary = notes.rate_limit_summary(log, baseline=1)
        self.assertEqual({"429_posts": 2, "429_same_body_hash": True, "429_statuses": [429, 202]}, summary)
        other = log[:1] + [dict(log[1]), {**log[2], "sha256": "other"}]
        self.assertFalse(notes.rate_limit_summary(other, baseline=1)["429_same_body_hash"])
        self.assertEqual(0, notes.rate_limit_summary(log[:1], baseline=1)["429_posts"])
        self.assertFalse(notes.rate_limit_summary(log[:1], baseline=1)["429_same_body_hash"])

    def test_notes_list_mock_drops_only_the_deleted_note_and_logs_no_titles(self):
        titles = [n["title"] for n in json.loads(self.call("GET", "/api/v1/notes")[1])]
        self.assertEqual(["Alpha list note", "Beta list note"], titles)
        self.assertEqual(204, self.call("DELETE", "/api/v1/notes/5eed0000-0000-4000-8000-0000000000a1")[0])
        left = json.loads(self.call("GET", "/api/v1/notes")[1])
        self.assertEqual(["5eed0000-0000-4000-8000-0000000000b1"], [n["document_id"] for n in left])
        self.assertNotIn("Alpha list note", json.dumps(notes.STATE["log"]))
        self.assertEqual(404, self.call("DELETE", "/api/v1/notes/unknown")[0])

    def test_offline_mock_drops_the_connection_unlogged_until_it_is_back_online(self):
        self.call("GET", "/__ctl/offline?on=1")
        with self.assertRaises(Exception):
            self.call("DELETE", "/api/v1/notes/5eed0000-0000-4000-8000-0000000000b1")
        self.assertEqual([], notes.STATE["log"])
        self.assertEqual([("DELETE", "/api/v1/notes/5eed0000-0000-4000-8000-0000000000b1")], [(e["method"], e["path"]) for e in notes.mock_dropped()])
        self.call("GET", "/__ctl/offline?on=0")
        self.assertEqual(204, self.call("DELETE", "/api/v1/notes/5eed0000-0000-4000-8000-0000000000b1")[0])
        self.assertEqual(1, forget.deletes_for(notes.STATE["log"]))


class OfflineForgetHelperTests(unittest.TestCase):
    DELETE = {"method": "DELETE", "path": "/api/v1/notes/5eed0000-0000-4000-8000-0000000000b1", "status": 204}

    def test_waiting_count_parses_the_line_and_is_none_when_hidden(self):
        self.assertEqual(1, forget.waiting_count(["Your notes", "1 forgotten note waiting for the server"]))
        self.assertEqual(3, forget.waiting_count(["3 forgotten notes waiting for the server"]))
        self.assertIsNone(forget.waiting_count(["Your notes", "Beta list note"]))

    def test_offline_summary(self):
        got = forget.offline_summary([], ["1 forgotten note waiting for the server"])
        self.assertEqual({"offline_waiting_count": 1, "offline_deletes": 0, "offline_row_hidden": True}, got)
        self.assertFalse(forget.offline_summary([], ["Beta list note"])["offline_row_hidden"])
        self.assertEqual(1, forget.offline_summary([self.DELETE], [])["offline_deletes"])

    def test_online_summary_counts_only_this_documents_deletes(self):
        other = {**self.DELETE, "path": "/api/v1/notes/5eed0000-0000-4000-8000-0000000000a1"}
        self.assertEqual({"online_deletes": 1, "online_waiting_gone": True}, forget.online_summary([self.DELETE, other], ["Your notes"]))
        self.assertFalse(forget.online_summary([self.DELETE], ["1 forgotten note waiting for the server"])["online_waiting_gone"])

    def test_scenario_verdict_needs_every_part(self):
        good = {"offline_waiting_count": 1, "offline_deletes": 0, "offline_row_hidden": True, "online_deletes": 1, "online_waiting_gone": True}
        self.assertTrue(forget.forget_scenario_passes(good))
        for key, bad in (("offline_waiting_count", 0), ("offline_deletes", 1), ("offline_row_hidden", False), ("online_deletes", 0), ("online_deletes", 2), ("online_waiting_gone", False)):
            self.assertFalse(forget.forget_scenario_passes({**good, key: bad}), key)
        self.assertFalse(forget.forget_scenario_passes({}))

    def test_replay_wait_is_bounded(self):
        self.assertLessEqual(forget.REPLAY_TIMEOUT, 180)
        self.assertGreaterEqual(forget.POLL_GAP, notes.MIN_POLL_GAP)


class NotesListHelperTests(unittest.TestCase):
    LOG = [{"method": "DELETE", "path": "/api/v1/notes/5eed0000-0000-4000-8000-0000000000a1", "status": 204}]

    def test_summary_when_only_the_forgotten_note_is_deleted(self):
        got = notes.list_summary(self.LOG, ["Your notes", "Beta list note"])
        self.assertEqual({"list_forget_deletes": 1, "list_other_deletes": 0, "list_other_row_remains": True, "list_forgotten_row_gone": True}, got)

    def test_summary_flags_a_stray_delete_a_missing_row_and_a_lingering_row(self):
        stray = self.LOG + [{"method": "DELETE", "path": "/api/v1/notes/5eed0000-0000-4000-8000-0000000000b1", "status": 204}]
        self.assertEqual(1, notes.list_summary(stray, ["Beta list note"])["list_other_deletes"])
        self.assertFalse(notes.list_summary(self.LOG, ["Your notes"])["list_other_row_remains"])
        self.assertFalse(notes.list_summary(self.LOG, ["Alpha list note", "Beta list note"])["list_forgotten_row_gone"])

    def test_find_last_prefers_the_dialog_button_over_the_row_button(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring('<hierarchy><node text="Forget" content-desc="" bounds="[0,0][1,1]"/><node text="Forget" content-desc="" bounds="[5,5][6,6]"/></hierarchy>')
        self.assertEqual("[5,5][6,6]", notes.find_last(root, r"^Forget\|")["bounds"])
        self.assertIsNone(notes.find_last(root, "Nope"))


if __name__ == "__main__":
    unittest.main()
