# SPDX-License-Identifier: GPL-3.0-only
"""Discovery acceptance helpers; these tests never call adb or touch a device."""
import ast
import inspect
import json
import io
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch
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
        with patch.object(discovery.notes.Device, "run", side_effect=[window_dump(), RuntimeError("private-token")]):
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

    def test_verdict_rejects_abort_and_runner_failure_after_all_scenarios(self):
        for failure in ("aborted", "runner_failed"):
            with self.subTest(failure=failure):
                results = {key: True for key in discovery.REQUIRED_RESULTS}
                results[failure] = True
                self.assertFalse(discovery.scenarios_pass(results))


def window_dump(focus=None, foreign=None, visible=True):
    package = discovery.notes.PACKAGE
    focus = focus or package + "/.MainActivity"
    dump = (f"mCurrentFocus=Window{{abc u0 {focus}}}\n"
            f"  Window #0 Window{{abc u0 {package}/.MainActivity}}:\n"
            f"    mOwnerUid=123 package={package}\n"
            "    mAttrs={ty=BASE_APPLICATION}\n"
            "    mHasSurface=true isOnScreen=true isVisible=true\n")
    if foreign:
        state = str(visible).lower()
        dump += (f"  Window #1 Window{{def u0 {foreign}/.Dialog}}:\n"
                 f"    mOwnerUid=456 package={foreign}\n"
                 f"    mHasSurface={state} isOnScreen={state} isVisible={state}\n")
    return dump


class InputSafetyTests(unittest.TestCase):
    def setUp(self):
        sleep = patch.object(discovery.notes.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        self.device = discovery.Device("unused", Path("/tmp"))
        self.root = ET.fromstring(
            f'<hierarchy><node package="{discovery.notes.PACKAGE}" text="Field" '
            'bounds="[0,0][100,100]"/></hierarchy>')

    def test_every_input_helper_checks_windows_immediately_before_input(self):
        helpers = [
            lambda: self.device.run("shell", "input", "keyevent", "KEYCODE_BACK"),
            lambda: self.device.shell("input", "keyevent", "KEYCODE_BACK"),
            lambda: self.device.type("private-token"),
            lambda: self.device.tap("Field"),
            lambda: self.device.tap_last("Field"),
            lambda: self.device.tap_scrolling("Field"),
            lambda: self.device.visible_scrolling("Missing", max_scrolls=1),
        ]
        for helper in helpers:
            with self.subTest(helper=helpers.index(helper)):
                calls = []
                def run(_device, *args, **_kwargs):
                    calls.append(args)
                    return window_dump() if args == ("shell", "dumpsys", "window", "windows") else ""
                with patch.object(discovery.notes.Device, "run", run), \
                     patch.object(self.device, "dump", return_value=self.root), \
                     patch.object(discovery.notes.time, "sleep"):
                    helper()
                inputs = [i for i, call in enumerate(calls) if call[:2] == ("shell", "input")]
                self.assertTrue(inputs)
                for index in inputs:
                    self.assertGreater(index, 0)
                    self.assertEqual(("shell", "dumpsys", "window", "windows"), calls[index - 1])

    def test_unsafe_windows_abort_without_input(self):
        for dump in ("", "mCurrentFocus=null", window_dump(focus="com.android.settings/.Dialog"),
                     window_dump(foreign="com.android.systemui"),
                     window_dump(foreign="com.google.android.inputmethod.latin"),
                     window_dump(foreign="com.example.autofill"),
                     window_dump().replace("BASE_APPLICATION", "APPLICATION_OVERLAY"),
                     window_dump().replace("ty=BASE_APPLICATION", "unknown=true"),
                     window_dump().replace("package=" + discovery.notes.PACKAGE,
                                           "package=com.google.android.inputmethod.latin").replace("BASE_APPLICATION", "INPUT_METHOD"),
                     window_dump().replace("isOnScreen=true isVisible=true", "unknown=true"),
                     window_dump().replace("package=", "unknown="),
                     window_dump().replace("isOnScreen=true isVisible=true", "isVisible=false"),
                     window_dump().split("  Window #")[0],
                     window_dump() + "mCurrentFocus=null\n"):
            with self.subTest(dump=dump):
                with patch.object(discovery.notes.Device, "run", return_value=dump) as run:
                    with self.assertRaises(discovery.notes.SupervisedAbort):
                        self.device.run("shell", "input", "text", "private-token")
                self.assertEqual([("shell", "dumpsys", "window", "windows")],
                                 [call.args for call in run.call_args_list])

    def test_read_failure_aborts_without_leaking_details(self):
        with patch.object(discovery.notes.Device, "run", side_effect=RuntimeError("private-window-title")) as run:
            with self.assertRaises(discovery.notes.SupervisedAbort) as caught:
                self.device.run("shell", "input", "text", "private-token")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(1, run.call_count)

    def test_input_is_paced_before_fresh_window_check(self):
        for entry in (self.device.run, self.device.shell):
            for keys in (("KEYCODE_BACK",), ("KEYCODE_DEL", "KEYCODE_BACK")):
                with self.subTest(entry=entry.__name__, keys=keys):
                    calls = []
                    def run(_device, *args, **_kwargs):
                        calls.append(args)
                        return window_dump()
                    with patch.object(discovery.notes.Device, "run", run), \
                         patch.dict(discovery.notes.STEP, {"last_input": 100}), \
                         patch.object(discovery.notes.time, "time", return_value=100), \
                         patch.object(discovery.notes.time, "sleep", side_effect=lambda _seconds: calls.append("paced")):
                        args = ("shell", "input") if entry.__name__ == "run" else ("input",)
                        entry(*args, "keyevent", *keys)
                    expected = []
                    for key in keys:
                        expected.extend(["paced", ("shell", "dumpsys", "window", "windows"),
                                         ("shell", "input", "keyevent", key)])
                    self.assertEqual(expected, calls)

    def test_hidden_foreign_window_is_safe(self):
        with patch.object(discovery.notes.Device, "run", return_value=window_dump(foreign="com.example.app", visible=False)) as run:
            self.device.run("shell", "input", "keyevent", "KEYCODE_BACK")
        self.assertEqual(2, run.call_count)

    def test_only_ordinary_system_bars_are_allowed(self):
        for title, kind in (("StatusBar", "STATUS_BAR"), ("NavigationBar0", "NAVIGATION_BAR")):
            bar = (f"  Window #1 Window{{def u0 {title}}}:\n"
                   "    mOwnerUid=1000 package=com.android.systemui\n"
                   f"    mAttrs={{ty={kind}}}\n"
                   f"    Frames: display=[0,0][1080,1920] frame={'[0,0][1080,64]' if kind == 'STATUS_BAR' else '[0,1856][1080,1920]'}\n"
                   "    mHasSurface=true isOnScreen=true isVisible=true\n")
            with self.subTest(kind=kind), patch.object(discovery.notes.Device, "run", return_value=window_dump() + bar) as run:
                self.device.run("shell", "input", "keyevent", "KEYCODE_BACK")
                self.assertEqual(2, run.call_count)
            with self.subTest(dialog=kind), patch.object(discovery.notes.Device, "run", return_value=window_dump() + bar.replace(title, "NotificationShade")) as run:
                with self.assertRaises(discovery.notes.SupervisedAbort):
                    self.device.run("shell", "input", "keyevent", "KEYCODE_BACK")
                self.assertEqual(1, run.call_count)

    def test_bars_do_not_depend_on_display_geometry(self):
        for kind, title in (("STATUS_BAR", "StatusBar"), ("NAVIGATION_BAR", "NavigationBar0")):
            for frame in ("", "Frames: display=[0,0][480,800] frame=[0,0][480,800]",
                          "Frames: display=[0,0][2560,1600] frame=[0,0][2560,1600]"):
                bar = (f"  Window #1 Window{{def u0 {title}}}:\n"
                       "    mOwnerUid=1000 package=com.android.systemui\n"
                       f"    mAttrs={{ty={kind}}}\n    {frame}\n"
                       "    isOnScreen=true isVisible=true\n")
                with self.subTest(kind=kind, frame=frame), patch.object(discovery.notes.Device, "run", return_value=window_dump() + bar):
                    self.device.shell("input", "keyevent", "KEYCODE_BACK")

    def test_ime_allowed_only_with_acceptance_focus_and_input_method_type(self):
        ime = ("  Window #1 Window{def u0 InputMethod}:\n"
               "    mOwnerUid=456 package=com.google.android.inputmethod.latin\n"
               "    mAttrs={ty=INPUT_METHOD}\n"
               "    isOnScreen=true isVisible=true\n")
        with patch.object(discovery.notes.Device, "run", return_value=window_dump() + ime) as run:
            self.device.type("private-token")
        self.assertEqual(2, run.call_count)
        for bad in (window_dump(focus="com.google.android.inputmethod.latin/.Ime") + ime,
                    window_dump() + ime.replace("INPUT_METHOD", "APPLICATION_OVERLAY"),
                    window_dump() + ime.replace("com.google.android.inputmethod.latin", "com.example.autofill"),
                    window_dump() + ime + window_dump(foreign="com.example.share")):
            with self.subTest(bad=bad), patch.object(discovery.notes.Device, "run", return_value=bad) as run:
                with self.assertRaises(discovery.notes.SupervisedAbort):
                    self.device.type("private-token")
                self.assertEqual(1, run.call_count)

    def test_all_am_pm_mutations_are_guarded_at_both_entrypoints(self):
        for command in (("am", "start", "-n", discovery.notes.ACTIVITY),
                        ("am", "kill", discovery.notes.PACKAGE),
                        ("am", "force-stop", discovery.notes.PACKAGE),
                        ("pm", "clear", discovery.notes.PACKAGE)):
            for entry in (self.device.shell, lambda *args: self.device.run("shell", *args)):
                with self.subTest(command=command), patch.object(discovery.notes.Device, "run", return_value=window_dump(foreign="com.example.autofill")) as run:
                    with self.assertRaises(discovery.notes.SupervisedAbort):
                        entry(*command)
                    self.assertEqual([("shell", "dumpsys", "window", "windows")], [call.args for call in run.call_args_list])
                calls = []
                def run(_device, *args, **kwargs):
                    calls.append(args)
                    return window_dump()
                with patch.object(discovery.notes.Device, "run", run):
                    entry(*command)
                self.assertEqual(("shell", "dumpsys", "window", "windows"), calls[-2])
                self.assertEqual(("shell", *command), calls[-1])

    def test_startup_precheck_and_each_connect_mutation(self):
        launcher = "com.example.launcher"
        launcher_dump = window_dump().replace(discovery.notes.PACKAGE, launcher)
        for unsafe in (False, True):
            calls = []
            def run(_device, *args, **kwargs):
                calls.append(args)
                if args[:3] == ("shell", "cmd", "package"):
                    return launcher + "/.Home\n"
                if args == ("shell", "dumpsys", "window", "windows"):
                    return window_dump(foreign="com.example.share") if unsafe else launcher_dump
                return ""
            def connect(dev, *_args):
                dev.shell("pm", "clear", discovery.notes.PACKAGE)
                dev.shell("am", "start", "-n", discovery.notes.ACTIVITY)
            with self.subTest(unsafe=unsafe), patch.object(discovery.notes.Device, "run", run), patch.object(discovery.notes, "connect", side_effect=connect):
                if unsafe:
                    with self.assertRaises(discovery.notes.SupervisedAbort):
                        self.device.connect(18767, {})
                else:
                    self.device.connect(18767, {})
            mutations = [i for i, c in enumerate(calls) if c[:2] in (("shell", "pm"), ("shell", "am"))]
            self.assertEqual(0 if unsafe else 2, len(mutations))
            for index in mutations:
                self.assertEqual(("shell", "dumpsys", "window", "windows"), calls[index - 1])
        with patch.object(discovery.notes.Device, "run", return_value=launcher_dump):
            with self.assertRaises(discovery.notes.SupervisedAbort):
                self.device.shell("am", "kill", discovery.notes.PACKAGE)
            with self.assertRaises(discovery.notes.SupervisedAbort):
                self.device.shell("input", "keyevent", "KEYCODE_BACK")

    def test_launcher_keyboard_and_failed_home_resolution_abort_before_connect(self):
        launcher_dump = window_dump().replace(discovery.notes.PACKAGE, "com.example.launcher")
        keyboard = ("  Window #1 Window{def u0 InputMethod}:\n"
                    "    mOwnerUid=456 package=com.google.android.inputmethod.latin\n"
                    "    mAttrs={ty=INPUT_METHOD}\n    isVisible=true\n")
        for home, windows in (("No activity found", launcher_dump),
                              ("android/.ResolverActivity", launcher_dump),
                              ("com.example.launcher/.Home", launcher_dump + keyboard)):
            calls = []
            def run(_device, *args, **kwargs):
                calls.append(args)
                return windows if args == ("shell", "dumpsys", "window", "windows") else home
            with self.subTest(home=home), patch.object(discovery.notes.Device, "run", run), patch.object(discovery.notes, "connect") as connect:
                with self.assertRaises(discovery.notes.SupervisedAbort):
                    self.device.connect(18767, {})
                connect.assert_not_called()
            self.assertFalse(any(c[:2] in (("shell", "am"), ("shell", "pm"), ("shell", "input")) for c in calls))

    def test_repository_mutating_shell_call_sites_use_the_guard(self):
        commands = set()
        for module in (discovery, discovery.notes):
            for call in ast.walk(ast.parse(inspect.getsource(module))):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                    continue
                args = [arg.value if isinstance(arg, ast.Constant) else "fixture" for arg in call.args]
                if call.func.attr == "run" and args[:1] == ["shell"]:
                    args = args[1:]
                elif call.func.attr != "shell":
                    continue
                if args and args[0] in ("input", "am", "pm"):
                    commands.add(tuple(args))
        self.assertTrue({"input", "am", "pm"}.issubset({c[0] for c in commands}))
        for command in commands:
            with self.subTest(command=command), patch.object(self.device, "check_input_window", side_effect=discovery.notes.SupervisedAbort("stop")) as guard, patch.object(discovery.notes.Device, "run") as transport:
                with self.assertRaises(discovery.notes.SupervisedAbort):
                    self.device.shell(*command)
                guard.assert_called_once()
                transport.assert_not_called()

    def test_inherited_restart_checks_home_kill_and_start_separately(self):
        for overlay in (False, True):
            calls = []
            def run(_device, *args, **kwargs):
                calls.append(args)
                if args[:3] == ("shell", "cmd", "package"):
                    return "com.example.launcher/.Home\n"
                if args == ("shell", "dumpsys", "window", "windows"):
                    if ("shell", "input", "keyevent", "KEYCODE_HOME") in calls:
                        return (window_dump(foreign="com.example.share") if overlay else
                                window_dump().replace(discovery.notes.PACKAGE, "com.example.launcher"))
                    return window_dump()
                return ""
            with self.subTest(overlay=overlay), patch.object(discovery.notes.Device, "run", run), patch.object(self.device, "pid", side_effect=["11", "22"]):
                if overlay:
                    with self.assertRaises(discovery.notes.SupervisedAbort):
                        self.device.kill_and_relaunch()
                else:
                    self.assertEqual(("11", "22"), self.device.kill_and_relaunch())
            mutations = [i for i, c in enumerate(calls) if c[:2] in (("shell", "input"), ("shell", "am"))]
            self.assertEqual(1 if overlay else 3, len(mutations))
            for index in mutations:
                self.assertEqual(("shell", "dumpsys", "window", "windows"), calls[index - 1])
            self.assertIsNone(self.device._startup_launcher)

    def test_mutation_transport_has_only_one_guarded_parent_call(self):
        # All inherited helpers must dispatch through self.shell/self.run; no direct
        # parent or subprocess transport may bypass the discovery guard.
        tree = ast.parse(inspect.getsource(discovery))
        parent_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute) and n.func.attr in ("run", "shell")
                        and isinstance(n.func.value, ast.Call) and isinstance(n.func.value.func, ast.Name)
                        and n.func.value.func.id == "super"]
        self.assertEqual(1, len(parent_calls))
        guard = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Device")
        guarded_run = next(n for n in guard.body if isinstance(n, ast.FunctionDef) and n.name == "run")
        self.assertIn(parent_calls[0], list(ast.walk(guarded_run)))
        self.assertIn("self.check_input_window(", ast.get_source_segment(inspect.getsource(discovery), guarded_run))
        for method in (discovery.notes.Device.shell, discovery.notes.Device.type,
                       discovery.notes.Device.tap, discovery.notes.Device.kill_and_relaunch):
            source = inspect.getsource(method)
            self.assertNotIn("subprocess", source)
            self.assertNotIn("Device.run(", source)
            self.assertNotIn("Device.shell(", source)

    def test_field_focus_overlay_prevents_delete_text_and_back(self):
        calls = []
        def run(_device, *args, **_kwargs):
            calls.append(args)
            if args == ("shell", "dumpsys", "window", "windows"):
                return window_dump(foreign="com.example.autofill") if any(c[:3] == ("shell", "input", "tap") for c in calls) else window_dump()
            return ""
        with patch.object(discovery.notes.Device, "run", run), \
             patch.object(self.device, "dump", return_value=self.root), \
             patch.object(discovery.notes.time, "sleep"):
            with self.assertRaises(discovery.notes.SupervisedAbort):
                self.device.tap("Field")
                self.device.shell("input", "keyevent", "KEYCODE_DEL")
                self.device.type("private-token")
                self.device.shell("input", "keyevent", "KEYCODE_BACK")
        self.assertEqual([("shell", "input", "tap", "50", "50")], [c for c in calls if c[:2] == ("shell", "input")])

    def test_each_batched_key_is_checked(self):
        calls = []
        def run(_device, *args, **_kwargs):
            calls.append(args)
            if args == ("shell", "dumpsys", "window", "windows"):
                return window_dump(foreign="com.example.autofill") if any(c[:2] == ("shell", "input") for c in calls) else window_dump()
            return ""
        with patch.object(discovery.notes.Device, "run", run):
            with self.assertRaises(discovery.notes.SupervisedAbort):
                self.device.run("shell", "input", "keyevent", "KEYCODE_DEL", "KEYCODE_DEL")
        self.assertEqual([("shell", "input", "keyevent", "KEYCODE_DEL")], [c for c in calls if c[:2] == ("shell", "input")])

    def test_final_evidence_failure_prints_fail(self):
        for error, code, failure in ((discovery.notes.SupervisedAbort("overlay"), 2, "aborted"),
                                     (RuntimeError("dump failed"), 3, "runner_failed")):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as out:
                device = Mock()
                device.shell.return_value = discovery.notes.PACKAGE
                device.run.return_value = ""
                device.dump.side_effect = error
                def scenarios(dev, _backend, results):
                    results.update({key: True for key in discovery.REQUIRED_RESULTS})
                    dev.dump("04-new-scope")
                output = io.StringIO()
                with patch.object(sys, "argv", ["android_discovery.py", "--serial", "unused", "--out", out]), \
                     patch.object(discovery, "Device", return_value=device), \
                     patch.object(discovery, "ThreadingHTTPServer"), \
                     patch.object(discovery.threading, "Thread"), \
                     patch.object(discovery.notes, "connect"), \
                     patch.object(discovery, "run_scenarios", side_effect=scenarios), \
                     patch.object(sys, "stdout", output):
                    self.assertEqual(code, discovery.main())
                self.assertEqual("Discovery acceptance: FAIL\n", output.getvalue())
                self.assertTrue(json.loads((Path(out) / "results.json").read_text())[failure])


if __name__ == "__main__":
    unittest.main()
