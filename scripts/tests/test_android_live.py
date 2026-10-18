"""Safety checks for the opt-in runner; no device or network access occurs."""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RUNNER = Path(__file__).resolve().parents[1] / "android_live.py"
spec = importlib.util.spec_from_file_location("android_live", RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

PACKAGE = "org.openlearning.app.acceptance"
TEST_CLASS = "org.openlearning.app.LiveLearningLoopTest"
INSTRUMENTATION = PACKAGE + ".test/androidx.test.runner.AndroidJUnitRunner"
CONFIG = "files/live-learning-loop.json"
PASS = "INSTRUMENTATION_STATUS: class=" + TEST_CLASS + "\nOK (1 test)\n"
TOKEN = "private-test-token"


class Response(io.BytesIO):
    status = 200

    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())


class AndroidLiveTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(
            mock.patch.object(
                runner, "__file__", str(Path(self.temp) / "scripts/android_live.py")
            )
        )
        self.env = self.stack.enter_context(
            mock.patch.dict(
                os.environ,
                {
                    "OL_API_TOKEN": TOKEN,
                    "OL_LIVE_ISOLATED": "1",
                    "OL_SERVER_URL": "http://127.0.0.1:8765",
                },
                clear=True,
            )
        )
        self.output = PASS
        self.reverse = ""
        self.config_exists = 1
        self.fail_command = None
        self.payloads = {}
        self.requests = []
        self.stdout = self.stack.enter_context(
            contextlib.redirect_stdout(io.StringIO())
        )
        self.stderr = self.stack.enter_context(
            contextlib.redirect_stderr(io.StringIO())
        )
        self.commands = self.stack.enter_context(
            mock.patch.object(runner.subprocess, "run", side_effect=self.adb)
        )
        self.opener = mock.Mock()
        self.opener.open.side_effect = self.get
        self.build_opener = self.stack.enter_context(
            mock.patch.object(
                runner.urllib.request, "build_opener", return_value=self.opener
            )
        )

    def adb(self, command, **kwargs):
        args = command[3:] if command[1:2] == ["-s"] else command[1:]
        code, output = 0, ""
        if self.fail_command and self.fail_command(args):
            code = 1
        elif args == ["devices"]:
            output = "List of devices attached\nemulator-5554\tdevice\n"
        elif args == ["shell", "pm", "list", "instrumentation"]:
            output = "instrumentation:" + INSTRUMENTATION
        elif args == ["shell", "run-as", PACKAGE, "pwd"]:
            output = "/data/user/0/" + PACKAGE
        elif args == ["shell", "run-as", PACKAGE, "test", "-e", CONFIG]:
            code = self.config_exists
        elif args == ["reverse", "--list"]:
            output = self.reverse
        elif args[:3] == ["shell", "am", "instrument"]:
            output = self.output
        return subprocess.CompletedProcess(
            command, code, stdout=output.encode(), stderr=b""
        )

    def get(self, request, **kwargs):
        self.requests.append(request)
        path = request.full_url.removeprefix("http://127.0.0.1:8765")
        default = {"status": "ok"} if path == "/api/v1/health" else []
        return Response(self.payloads.get(path, default))

    def run_main(self, *args):
        with mock.patch("sys.argv", [str(RUNNER), *args]):
            return runner.main()

    def args(self):
        return [
            call.args[0][3:] if call.args[0][1:2] == ["-s"] else call.args[0][1:]
            for call in self.commands.call_args_list
        ]

    def assert_cleanup(self):
        self.assertIn(["shell", "run-as", PACKAGE, "rm", "-f", CONFIG], self.args())
        self.assertIn(["reverse", "--remove", "tcp:8765"], self.args())

    def test_explicit_opt_in_and_isolation_are_both_required(self):
        self.assertEqual(self.run_main(), 1)
        self.opener.open.assert_not_called()
        self.commands.assert_not_called()
        os.environ.pop("OL_LIVE_ISOLATED")
        self.assertEqual(self.run_main("--allow-live"), 1)
        self.opener.open.assert_not_called()
        self.commands.assert_not_called()

    def test_rejects_unsafe_backend_urls_before_authenticated_request(self):
        for url in (
            "https://127.0.0.1:8765",
            "http://example.com:8765",
            "http://127.0.0.1.evil.test:8765",
            "http://user@localhost:8765",
            "http://user:pass@localhost:8765",
            "http://localhost:8765?x=1",
            "http://localhost:8765#secret",
            "http://localhost:8765/api",
        ):
            with self.subTest(url=url):
                os.environ["OL_SERVER_URL"] = url
                self.assertEqual(self.run_main("--allow-live"), 1)
                self.opener.open.assert_not_called()
                self.commands.assert_not_called()

    def test_authenticated_redirects_are_disabled_and_proxy_is_bypassed(self):
        self.assertEqual(self.run_main("--allow-live"), 0)
        proxy, redirect = self.build_opener.call_args.args
        self.assertEqual(proxy.proxies, {})
        request = self.requests[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.assertIsNone(
                    redirect.redirect_request(
                        request, None, status, "redirect", {}, "https://example.com/"
                    )
                )
        self.assertEqual([r.get_method() for r in self.requests], ["GET"] * 5)

    def test_refuses_any_nonempty_backend_queue_before_adb(self):
        for path in (
            "/api/v1/feed/today",
            "/api/v1/library",
            "/api/v1/history",
            "/api/v1/reviews/due",
        ):
            with self.subTest(path=path):
                self.payloads = {path: [{"id": "already-present"}]}
                self.assertEqual(self.run_main("--allow-live"), 1)
                self.commands.assert_not_called()

    def test_preexisting_private_config_is_not_overwritten_or_removed(self):
        self.config_exists = 0
        self.assertEqual(self.run_main("--allow-live"), 1)
        self.assertFalse(
            any(
                "reverse" in args or "rm" in args or "sh" in args
                for args in self.args()
            )
        )

    def test_preexisting_reverse_is_neither_replaced_nor_removed(self):
        self.reverse = "emulator-5554 tcp:8765 tcp:4321\n"
        self.assertEqual(self.run_main("--allow-live"), 1)
        self.assertEqual(
            [args for args in self.args() if args[0] == "reverse"],
            [["reverse", "--list"]],
        )
        self.assertFalse(any("sh" in args or "rm" in args for args in self.args()))

    def test_success_uses_private_stdin_and_cleans_owned_resources(self):
        self.output += TOKEN
        self.assertEqual(self.run_main("--allow-live"), 0)
        self.assert_cleanup()
        self.assertIn(["reverse", "--no-rebind", "tcp:8765", "tcp:8765"], self.args())
        writes = [
            call for call in self.commands.call_args_list if call.kwargs.get("input")
        ]
        self.assertEqual(len(writes), 1)
        config = json.loads(writes[0].kwargs["input"])
        self.assertEqual(config["token"], TOKEN)
        self.assertEqual(config["server_url"], "http://127.0.0.1:8765")
        self.assertIs(config["isolated_backend"], True)
        for call in self.commands.call_args_list:
            self.assertNotIn(TOKEN, str(call.args[0]))
            self.assertNotIn("OL_API_TOKEN", call.kwargs["env"])
        report = Path(self.temp) / ".omc/android-live-instrumentation.log"
        self.assertEqual(report.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(TOKEN, report.read_text())

    def test_instrumentation_failure_or_skip_fails_even_with_adb_zero(self):
        for output in (
            "",
            "OK (0 tests)",
            "OK (2 tests)",
            PASS + "FAILURES!!!",
            PASS + "INSTRUMENTATION_FAILED",
            PASS + "INSTRUMENTATION_STATUS_CODE: -2",
            PASS + "INSTRUMENTATION_STATUS_CODE: -1",
            PASS + "INSTRUMENTATION_STATUS_CODE: -3",
            PASS + "INSTRUMENTATION_STATUS_CODE: -4",
            "OK (1 test)\n",
        ):
            with self.subTest(output=output):
                self.commands.reset_mock()
                self.output = output
                self.assertEqual(self.run_main("--allow-live"), 1)
                self.assert_cleanup()

    def test_partial_configuration_write_still_cleans_up(self):
        self.fail_command = lambda args: "sh" in args
        self.assertEqual(self.run_main("--allow-live"), 1)
        self.assert_cleanup()

    def test_cleanup_failure_changes_passing_run_to_failure(self):
        for failing in ("rm", "--remove"):
            with self.subTest(command=failing):
                self.commands.reset_mock()
                self.fail_command = lambda args, failing=failing: failing in args
                self.assertEqual(self.run_main("--allow-live"), 1)
                self.assert_cleanup()


if __name__ == "__main__":
    unittest.main()
