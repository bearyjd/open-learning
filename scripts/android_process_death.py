"""Adb-driven process-death check for the acceptance app (org.openlearning.app.acceptance).

Starts a throwaway loopback mock of the /api/v1 contract on this host (no
database, no model, no cloud), forwards it with its own `adb reverse`, drives
the already-installed acceptance app through the UI, kills the app process with
`am kill` while it is backgrounded, relaunches it, and prints/saves uiautomator
evidence. It never reboots, resizes, folds, or changes any device setting, and
touches only the acceptance package. Install the APK first:

    adb install -r android/app/build/outputs/apk/acceptance/app-acceptance.apk
    python3 scripts/android_process_death.py --serial SERIAL [--port 18765]

The script clears the acceptance app's data at the start (`pm clear`), removes
only its own adb reverse mapping, and stops its mock server on exit.
Evidence (XML dumps, screenshots, request log) goes to --out (default
.omc/process-death/, ignored by Git).
"""

import argparse
import json
import re
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PACKAGE = "org.openlearning.app.acceptance"
ACTIVITY = PACKAGE + "/org.openlearning.app.MainActivity"
TOKEN = "process-death-local-token"
IDEA = {
    "id": "idea-1", "title": "Spaced practice beats cramming",
    "idea": "Recall improves with spaced practice.",
    "explanation": "Reviewing material over widening intervals strengthens memory.",
    "why_it_matters": "It makes study time count.", "source_title": "Fixture source",
    "source_url": "https://example.com/spacing", "document_id": "doc-1",
    "evidence": "Spacing effects are robust.", "tags": ["memory"], "saved": False,
    "coverage": "full article", "source_truncated": False, "model": "mock",
}
LOCK = threading.Lock()
STATE = {"saved": False, "job_done": False, "log": []}


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, body, code=200):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self, method):
            path = self.path.split("?")[0]
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            if path == "/__ctl/complete":
                with LOCK:
                    STATE["job_done"] = True
                return self._send({"ok": True})
            if path == "/__ctl/log":
                with LOCK:
                    return self._send(STATE["log"])
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                return self._send({"detail": "denied"}, 401)
            with LOCK:
                STATE["log"].append([round(time.time(), 1), method, self.path])
                saved, done = STATE["saved"], STATE["job_done"]
            idea = dict(IDEA, saved=saved)
            routes = {
                ("GET", "/api/v1/feed/today"): [idea],
                ("GET", "/api/v1/library"): [idea] if saved else [],
                ("GET", "/api/v1/insights/idea-1"): idea,
                ("GET", "/api/v1/search"): [idea],
                ("GET", "/api/v1/jobs/remote-1"): {
                    "id": "remote-1", "status": "completed" if done else "processing"},
                ("POST", "/api/v1/ingest/url"): {"id": "remote-1", "status": "processing"},
                ("POST", "/api/v1/tutor/chat"): {
                    "answer": "Spacing helps.", "question": "Why does spacing help?",
                    "citations": [], "review_id": "rev-1"},
                ("GET", "/api/v1/reviews/due"): [{
                    "id": "rev-1", "insight_id": "idea-1", "question": "Why space practice?",
                    "due_at": "2026-10-19T00:00:00Z", "interval_days": 1.0,
                    "insight_title": IDEA["title"]}],
            }
            if (method, path) == ("POST", "/api/v1/insights/idea-1/save"):
                with LOCK:
                    STATE["saved"] = True
                return self._send(dict(IDEA, saved=True))
            if (method, path) in routes:
                return self._send(routes[(method, path)])
            self._send({"detail": "not found"}, 404)

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

    return Handler


class Device:
    def __init__(self, serial, out):
        self.prefix = ["adb", "-s", serial]
        self.out = out

    def run(self, *args, check=True):
        result = subprocess.run([*self.prefix, *args], capture_output=True, text=True, timeout=60)
        if check and result.returncode != 0:
            raise RuntimeError("adb " + " ".join(args) + " failed: " + result.stderr.strip())
        return result.stdout

    def shell(self, *args, check=True):
        return self.run("shell", *args, check=check)

    def pid(self):
        return self.shell("pidof", PACKAGE, check=False).strip()

    def dump(self, name=None):
        raw = self.run("exec-out", "uiautomator", "dump", "/dev/tty")
        raw = raw[raw.index("<?xml"): raw.rindex("</hierarchy>") + len("</hierarchy>")]
        root = ET.fromstring(raw)
        # An autofill service may prompt to save the typed token; decline once ("No thanks" is not "Never").
        for n in root.iter("node"):
            if n.get("text") == "No thanks" and n.get("package") == "android":
                x1, y1, x2, y2 = map(int, re.findall(r"\d+", n.attrib["bounds"]))
                self.shell("input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
                time.sleep(1.5)
                return self.dump(name)
        if not any(n.get("package") == PACKAGE for n in root.iter("node")):
            raise RuntimeError("acceptance app is not in the foreground (shade/lockscreen/other app); aborting")
        if name:
            (self.out / (name + ".xml")).write_text(raw)
            info = self.shell("dumpsys", "display")
            ids = re.findall(r'uniqueId="local:(\d+)"[^\n]*?state ON', info)
            display = ["-d", ids[0]] if ids else []
            png = subprocess.run([*self.prefix, "exec-out", "screencap", *display, "-p"], capture_output=True, timeout=60)
            (self.out / (name + ".png")).write_bytes(png.stdout)
        return root

    def nodes(self, root=None):
        root = root if root is not None else self.dump()
        return [n.attrib for n in root.iter("node")]

    def texts(self, root=None):
        found = []
        for n in self.nodes(root):
            found += [v for v in (n.get("text"), n.get("content-desc")) if v]
        return found

    def find(self, pattern, root=None, cls=None):
        for n in self.nodes(root):
            label = (n.get("text") or "") + "|" + (n.get("content-desc") or "")
            if re.search(pattern, label) and (cls is None or n.get("class") == cls):
                return n
        return None

    def tap(self, pattern, cls=None, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            node = self.find(pattern, cls=cls)
            if node:
                x1, y1, x2, y2 = map(int, re.findall(r"\d+", node["bounds"]))
                self.shell("input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
                time.sleep(0.8)
                return
            time.sleep(0.7)
        raise RuntimeError("UI element not found: " + pattern)

    def type(self, text):
        self.shell("input", "text", text.replace(" ", "%s"))
        time.sleep(0.5)

    def wait_for(self, pattern, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if self.find(pattern):
                return True
            time.sleep(0.7)
        return False

    def kill_and_relaunch(self, label):
        self.shell("input", "keyevent", "KEYCODE_HOME")
        time.sleep(2)
        before = self.pid()
        print("[%s] kill at %.1f" % (label, time.time()))
        self.shell("am", "kill", PACKAGE)
        time.sleep(2)
        after_kill = self.pid()
        self.shell("am", "start", "-n", ACTIVITY)
        time.sleep(4)
        after = self.pid()
        print("[%s] pid before=%s after_kill=%s after_relaunch=%s" % (label, before, after_kill or "-", after))
        if not before or before == after:
            raise RuntimeError("process was not killed (pid unchanged); results invalid")
        return before, after


def scenario(dev, name, setup, expect_gone, expect_kept):
    setup()
    dev.dump(name + "-before")
    kill = dev.kill_and_relaunch(name)
    root = dev.dump(name + "-after")
    texts = dev.texts(root)
    result = {
        "scenario": name, "pids": kill,
        "survived": [p for p in expect_kept if dev.find(p, root)],
        "lost": [p for p in expect_gone if not dev.find(p, root)],
        "unexpected_kept": [p for p in expect_gone if dev.find(p, root)],
        "missing": [p for p in expect_kept if not dev.find(p, root)],
    }
    print(json.dumps(result))
    return result, texts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / ".omc" / "process-death"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dev = Device(args.serial, out)
    remote = "tcp:%d" % args.port
    owns_reverse = False
    server = None
    results = []
    try:
        if PACKAGE not in dev.shell("pm", "list", "packages", PACKAGE):
            raise RuntimeError("Install the acceptance APK first (see module docstring)")
        if remote in dev.run("reverse", "--list"):
            raise RuntimeError("adb reverse already exists for " + remote + "; choose another --port")
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        dev.run("reverse", "--no-rebind", remote, remote)
        owns_reverse = True
        dev.shell("pm", "clear", PACKAGE)
        dev.shell("am", "start", "-n", ACTIVITY)
        dev.wait_for("Connect your server")
        dev.tap("Server URL")
        dev.type("http://127.0.0.1:%d" % args.port)
        dev.tap("Access token")
        dev.type(TOKEN)
        dev.shell("input", "keyevent", "KEYCODE_BACK")  # close the IME only
        time.sleep(1)
        box = dev.find("Allow HTTP")  # the label is not clickable; tap the checkbox to its left
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", box["bounds"]))
        dev.shell("input", "tap", str(x1 - 50), str((y1 + y2) // 2))
        time.sleep(0.8)
        dev.tap(r"^Connect\|")
        if not dev.wait_for("Spaced practice beats cramming"):
            raise RuntimeError("feed card never appeared; connection failed")
        dev.tap(r"^Save\|")
        dev.wait_for("Saved")
        dev.tap("Article URL")
        dev.type("https://example.com/queued-article")
        dev.shell("input", "keyevent", "KEYCODE_BACK")
        time.sleep(1)
        dev.tap("Add to learning feed")
        dev.wait_for("Processing")
        dev.dump("00-baseline-job-processing")

        def review():
            dev.tap(r"^Review\|")
            dev.tap("Show explanation")

        def explore():
            dev.tap(r"^Explore\|")
            dev.tap("Search ideas")
            dev.type("spaced")

        def tutor():
            dev.tap(r"^Learn\|")
            dev.tap(r"Start learning")
            dev.wait_for("Explain it in your own words")
            dev.tap("Explain it in your own words")
            dev.type("unsent draft")

        results.append(scenario(dev, "01-review-revealed", review,
                                ["How well did you remember"], ["Bring it back"]))
        results.append(scenario(dev, "02-explore-query", explore,
                                ["spaced"], ["Connect the dots"]))
        results.append(scenario(dev, "03-tutor-unsent", tutor,
                                ["Explain it in your own words", "unsent draft"], ["A little deeper"]))
        # Background resumption without a user relaunch: kill, then wait for WorkManager's next poll.
        dev.shell("input", "keyevent", "KEYCODE_HOME")
        time.sleep(2)
        pid_before = dev.pid()
        with LOCK:
            seen = len(STATE["log"])
        dev.shell("am", "kill", PACKAGE)
        print("[04-wm-resume] killed pid %s at %.1f; no relaunch, waiting <=240s for a job poll" % (pid_before, time.time()))
        poll = None
        end = time.time() + 240
        while time.time() < end and poll is None:
            time.sleep(3)
            with LOCK:
                poll = next((e for e in STATE["log"][seen:] if "/jobs/" in e[2]), None)
        print("[04-wm-resume] poll without relaunch:", poll, "pid now:", dev.pid() or "-")
        with LOCK:
            STATE["job_done"] = True
        dev.shell("am", "start", "-n", ACTIVITY)
        time.sleep(3)
        completed = False
        end = time.time() + 240
        while time.time() < end and not completed:
            dev.tap(r"^Discover\|")
            completed = dev.wait_for("Completed", timeout=10)
        dev.dump("04-job-completed")
        print("job shows Completed in UI after backend completes:", completed)
        dev.tap(r"^Library\|")
        print("saved card in Library after 4 kills:", bool(dev.wait_for("Spaced practice beats cramming")))
        dev.dump("05-library")
        with LOCK:
            jobs = [e for e in STATE["log"] if "/jobs/" in e[2] or "ingest" in e[2]]
        print("ingest/job requests:", json.dumps(jobs))
        (out / "request-log.json").write_text(json.dumps(STATE["log"], indent=1))
    finally:
        if owns_reverse:
            dev.run("reverse", "--remove", remote, check=False)
        if server:
            server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
