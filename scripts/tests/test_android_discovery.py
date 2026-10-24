# SPDX-License-Identifier: GPL-3.0-only
"""Discovery acceptance helpers; these tests never call adb or touch a device."""
import json
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import android_discovery as discovery


class MockTests(unittest.TestCase):
    def setUp(self):
        self.mock = discovery.MockBackend()

    def call(self, method, path, payload=None, token=discovery.TOKEN):
        raw = json.dumps(payload).encode() if payload is not None else b""
        return self.mock.request(method, path, raw, token)

    def test_queue_newest_pagination_and_decisions(self):
        code, page, _ = self.call("GET", "/api/v1/discover?sort=newest")
        self.assertEqual(200, code)
        self.assertEqual(discovery.NEWEST_ID, page["items"][0]["id"])
        self.assertTrue(page["next_cursor"])
        _, second, _ = self.call("GET", "/api/v1/discover?cursor=" + page["next_cursor"])
        self.assertNotEqual(page["items"][0]["id"], second["items"][0]["id"])
        self.assertEqual(202, self.call("POST", "/api/v1/discover/" + discovery.NEWEST_ID + "/ingest")[0])
        self.assertEqual(409, self.call("POST", "/api/v1/discover/" + discovery.NEWEST_ID + "/ingest")[0])
        self.assertEqual(204, self.call("POST", "/api/v1/discover/" + discovery.RELEVANT_ID + "/dismiss")[0])
        self.assertEqual([], self.call("GET", "/api/v1/discover")[1]["items"])

    def test_relevance_stale_cursor_restarts_once(self):
        _, page, _ = self.call("GET", "/api/v1/discover?sort=relevance")
        self.assertEqual(discovery.RELEVANT_ID, page["items"][0]["id"])
        path = "/api/v1/discover?sort=relevance&cursor=" + page["next_cursor"]
        self.assertEqual(409, self.call("GET", path)[0])
        self.assertEqual(200, self.call("GET", "/api/v1/discover?sort=relevance")[0])
        self.assertEqual(200, self.call("GET", path)[0])

    def test_feed_add_duplicate_refresh_rate_limit_and_remove(self):
        payload = {"url": discovery.FEED_URL}
        self.assertEqual(201, self.call("POST", "/api/v1/feeds", payload)[0])
        self.assertEqual(409, self.call("POST", "/api/v1/feeds", payload)[0])
        path = "/api/v1/feeds/" + discovery.ADDED_FEED_ID
        code, _, headers = self.call("POST", path + "/refresh")
        self.assertEqual((429, "1"), (code, headers["Retry-After"]))
        self.assertEqual(200, self.call("POST", path + "/refresh")[0])
        self.assertEqual(204, self.call("DELETE", path)[0])
        self.assertEqual(1, len(self.call("GET", "/api/v1/feeds")[1]))

    def test_pin_unpin_and_scope_isolation(self):
        _, pin, _ = self.call("POST", "/api/v1/topics/pinned", {"term": discovery.PIN_TERM})
        self.assertEqual(discovery.PIN_TERM, self.call("GET", "/api/v1/topics")[1]["pinned"][0]["term"])
        self.assertEqual([], self.call("GET", "/api/v1/topics", token=discovery.SECOND_TOKEN)[1]["pinned"])
        self.assertEqual([], self.call("GET", "/api/v1/discover", token=discovery.SECOND_TOKEN)[1]["items"])
        self.assertEqual(204, self.call("DELETE", "/api/v1/topics/pinned/" + pin["id"])[0])
        self.assertEqual([], self.call("GET", "/api/v1/topics")[1]["pinned"])

    def test_auth_and_evidence_never_keep_queries_bodies_or_credentials(self):
        self.assertEqual(401, self.call("GET", "/api/v1/topics?private=secret", token="wrong")[0])
        self.call("POST", "/api/v1/topics/pinned", {"term": discovery.PIN_TERM})
        encoded = json.dumps(self.mock.log)
        for value in ("secret", discovery.TOKEN, discovery.PIN_TERM, "?", "Authorization"):
            self.assertNotIn(value, encoded)
        self.assertEqual({"method", "path", "status", "len", "sha256"}, set(self.mock.log[0]))

    def test_unknown_private_path_is_redacted(self):
        self.call("GET", "/api/v1/topics/private-secret?term=hidden")
        self.assertEqual("<unrecognized>", self.mock.log[-1]["path"])
        self.assertNotIn("private-secret", json.dumps(self.mock.log))

    def test_adb_failures_do_not_expose_argument_or_stderr_secrets(self):
        device = discovery.Device("unused", Path("/tmp"))
        with patch.object(discovery.notes.Device, "run", side_effect=RuntimeError("private-token")):
            with self.assertRaisesRegex(RuntimeError, "^adb operation failed$"):
                device.run("shell", "input", "text", "private-token")

    def test_overlay_abort_keeps_stop_semantics_without_private_text(self):
        device = discovery.Device("unused", Path("/tmp"))
        with patch.object(discovery.notes.Device, "dump", side_effect=discovery.notes.SupervisedAbort("private-title")):
            with self.assertRaises(discovery.notes.SupervisedAbort) as caught:
                device.dump()
        self.assertNotIn("private-title", str(caught.exception))

    def test_verdict_requires_all_scenarios(self):
        self.assertFalse(discovery.scenarios_pass({}))
        results = {key: True for key in discovery.REQUIRED_RESULTS}
        self.assertTrue(discovery.scenarios_pass(results))
        results["scope_cleared"] = False
        self.assertFalse(discovery.scenarios_pass(results))


if __name__ == "__main__":
    unittest.main()
