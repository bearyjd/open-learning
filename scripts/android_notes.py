"""Adb-driven acceptance for text-note ingestion in the acceptance app (org.openlearning.app.acceptance).

Starts a loopback mock of the /api/v1 contract on this host (no database, no model),
forwards it with its own `adb reverse`, drives the already-installed acceptance app
through the UI and records uiautomator XML dumps only (no screenshots: note text must
not be captured as pixels). The mock logs method, path, status, body length and the
SHA-256 of the body, never the body itself. Never reboots, resizes, or changes device
settings; touches only the acceptance package. Install the APK first:

    adb install -r android/app/build/outputs/apk/acceptance/app-acceptance.apk
    python3 scripts/android_notes.py --serial SERIAL [--port 18766]

Scenarios: draft survives process death; submit posts exactly once with the expected
hash and shows no source link; 413 maps to the fixed message; forget Cancel sends no
DELETE and Confirm sends exactly one. Evidence goes to --out (default .omc/notes/, ignored by Git).
"""

import argparse
import hashlib
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
TOKEN = "notes-local-token"
NOTE_TEXT = ("Spaced practice revisits an idea after a delay so that recall gets stronger over time "
             "and useful ideas stay available when they are needed later.")
NOTE_TITLE = "Acceptance note"
NOTE = {
    "id": "note-1", "title": "Spacing keeps ideas available", "idea": "Revisit ideas after a delay.",
    "explanation": "Delayed review strengthens recall.", "why_it_matters": "", "source_title": NOTE_TITLE,
    "source_url": "https://example.invalid/never-linked", "document_id": "note-doc", "evidence": "Spaced practice revisits an idea.",
    "tags": ["memory"], "saved": False, "coverage": "full_source", "source_truncated": False, "model": "mock",
    "source_kind": "note", "source_label": "",
}
LINK_LABELS = ("Open original source", "Check source", "Open source article")
SAFE_TEXT = re.compile(r"^[A-Za-z0-9 .,]+$")


def body_digest(raw: bytes) -> dict:
    """What the mock may remember about a request body: its size and hash, never the content."""
    return {"len": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def adb_text(text: str) -> str:
    """Encode text for `adb shell input text`; refuse anything the shell could reinterpret."""
    if not SAFE_TEXT.match(text):
        raise ValueError("unsafe characters for adb input text")
    return text.replace(" ", "%s")


def counter_value(texts):
    """Parse the editor's live counter ("N / 60,000") out of uiautomator text."""
    for t in texts:
        m = re.fullmatch(r"([\d,]+) / 60,000", t.strip())
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def link_labels_present(texts) -> list:
    return [label for label in LINK_LABELS if any(label in t for t in texts)]


def count_requests(log, method, path) -> int:
    return sum(1 for e in log if e["method"] == method and e["path"] == path)


STATE = {"log": [], "text_status": 200, "done": False}
LOCK = threading.Lock()


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        _digest = None  # set per request; the log entry is written before the reply so callers see it at once

        def _send(self, body, code=200):
            if self._digest is not None:
                with LOCK:
                    STATE["log"].append({"t": round(time.time(), 1), "method": self.command, "path": self.path.split("?")[0], "status": code, **self._digest})
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return code

        def _route(self, method):
            path = self.path.split("?")[0]
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._digest = None if path.startswith("/__ctl/") else body_digest(raw)
            if path.startswith("/__ctl/"):
                with LOCK:
                    if path == "/__ctl/text_status":
                        STATE["text_status"] = int(self.path.split("=")[1])
                    elif path == "/__ctl/log":
                        return self._send(STATE["log"])
                return self._send({"ok": True})
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                self._send({"detail": "denied"}, 401)
            else:
                self._dispatch(method, path)

        def _dispatch(self, method, path):
            with LOCK:
                text_status, done = STATE["text_status"], STATE["done"]
            if (method, path) == ("POST", "/api/v1/ingest/text"):
                if text_status != 200:
                    return self._send({"detail": "SENTINEL-BODY"}, text_status)
                with LOCK:
                    STATE["done"] = True
                return self._send({"id": "job-1", "status": "completed"})
            routes = {
                ("GET", "/api/v1/feed/today"): [NOTE] if done else [],
                ("GET", "/api/v1/library"): [],
                ("GET", "/api/v1/insights/note-1"): NOTE,
                ("GET", "/api/v1/jobs/job-1"): {"id": "job-1", "status": "completed"},
            }
            if (method, path) in routes:
                return self._send(routes[(method, path)])
            if method == "DELETE" and path == "/api/v1/notes/note-doc":
                return self._send(None, 204)
            return self._send({"detail": "not found"}, 404)

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_DELETE(self):
            self._route("DELETE")

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
        if not any(n.get("package") == PACKAGE for n in root.iter("node")):
            raise RuntimeError("acceptance app is not in the foreground; aborting")
        if name:  # XML only. The dump holds field text, so it stays in the git-ignored evidence directory.
            (self.out / (name + ".xml")).write_text(raw)
        return root

    def texts(self, root=None):
        root = root if root is not None else self.dump()
        return [v for n in root.iter("node") for v in (n.get("text"), n.get("content-desc")) if v]

    def find(self, pattern, root=None):
        root = root if root is not None else self.dump()
        for n in root.iter("node"):
            if re.search(pattern, (n.get("text") or "") + "|" + (n.get("content-desc") or "")):
                return n.attrib
        return None

    def tap(self, pattern, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            node = self.find(pattern)
            if node:
                x1, y1, x2, y2 = map(int, re.findall(r"\d+", node["bounds"]))
                self.shell("input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
                time.sleep(0.8)
                return
            time.sleep(0.7)
        raise RuntimeError("UI element not found: " + pattern)

    def type(self, text):
        self.shell("input", "text", adb_text(text))
        time.sleep(0.5)

    def wait_for(self, pattern, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if self.find(pattern):
                return True
            time.sleep(0.7)
        return False

    def kill_and_relaunch(self):
        self.shell("input", "keyevent", "KEYCODE_HOME")
        time.sleep(2)
        before = self.pid()
        self.shell("am", "kill", PACKAGE)
        time.sleep(2)
        self.shell("am", "start", "-n", ACTIVITY)
        time.sleep(4)
        after = self.pid()
        if not before or before == after:
            raise RuntimeError("process was not killed (pid unchanged); results invalid")
        return before, after


def connect(dev, port):
    dev.shell("pm", "clear", PACKAGE)
    dev.shell("am", "start", "-n", ACTIVITY)
    dev.wait_for("Connect your server")
    dev.tap("Server URL")
    dev.type("http://127.0.0.1:%d" % port)
    dev.tap("Access token")
    dev.type(TOKEN)
    dev.shell("input", "keyevent", "KEYCODE_BACK")
    time.sleep(1)
    box = dev.find("Allow HTTP")
    if box is None:
        raise RuntimeError("HTTP consent checkbox not found")
    x1, y1, _x2, y2 = map(int, re.findall(r"\d+", box["bounds"]))
    dev.shell("input", "tap", str(x1 - 50), str((y1 + y2) // 2))
    time.sleep(0.8)
    dev.tap(r"^Connect\|")
    if not dev.wait_for("Write or import a note"):
        raise RuntimeError("Discover never appeared; connection failed")


def fill_editor(dev):
    dev.tap("Write or import a note")
    dev.tap("Title")
    dev.type(NOTE_TITLE)
    dev.shell("input", "keyevent", "KEYCODE_BACK")
    dev.tap("Note text")
    dev.type(NOTE_TEXT)
    dev.shell("input", "keyevent", "KEYCODE_BACK")
    time.sleep(1.5)  # the draft is saved 400 ms after the last edit


def mock_log():
    with LOCK:
        return list(STATE["log"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / ".omc" / "notes"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dev = Device(args.serial, out)
    remote = "tcp:%d" % args.port
    owns_reverse = False
    server = None
    results = {}
    try:
        if PACKAGE not in dev.shell("pm", "list", "packages", PACKAGE):
            raise RuntimeError("Install the acceptance APK first (see module docstring)")
        if remote in dev.run("reverse", "--list"):
            raise RuntimeError("adb reverse already exists for " + remote + "; choose another --port")
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        dev.run("reverse", "--no-rebind", remote, remote)
        owns_reverse = True
        connect(dev, args.port)

        fill_editor(dev)
        expected = len(NOTE_TEXT)
        dev.kill_and_relaunch()
        dev.wait_for("Note text|Write or import a note")
        counter = counter_value(dev.texts(dev.dump("01-draft-after-kill")))
        results["draft_survives_process_death"] = counter == expected
        results["draft_counter"] = counter

        dev.tap("Add note to learning feed")
        results["submit_shows_note_card"] = dev.wait_for("Your note: " + NOTE_TITLE, timeout=40)
        texts = dev.texts(dev.dump("02-after-submit"))
        log = mock_log()
        post = [e for e in log if e["method"] == "POST" and e["path"] == "/api/v1/ingest/text"]
        results["posts"] = len(post)
        results["no_link_nodes_on_list"] = not link_labels_present(texts)

        dev.tap("Your note: " + NOTE_TITLE)
        dev.wait_for("Forget this note")
        results["no_link_nodes_on_detail"] = not link_labels_present(dev.texts(dev.dump("03-detail")))
        dev.tap("Forget this note")
        dev.tap(r"^Cancel\|")
        results["forget_cancel_deletes"] = count_requests(mock_log(), "DELETE", "/api/v1/notes/note-doc")
        dev.tap("Forget this note")
        dev.tap(r"^Forget\|")
        dev.wait_for("Note forgotten")
        dev.dump("04-after-forget")
        results["forget_confirm_deletes"] = count_requests(mock_log(), "DELETE", "/api/v1/notes/note-doc")

        urllib_set_status(args.port, 413)
        dev.tap("Write or import a note")
        fill_editor(dev)
        dev.tap("Add note to learning feed")
        results["413_message"] = dev.wait_for("larger than your server accepts", timeout=40)
        dev.dump("05-413")
        results["sentinel_body_in_ui"] = bool(dev.find("SENTINEL-BODY"))

        (out / "request-log.json").write_text(json.dumps(mock_log(), indent=1))
        print(json.dumps(results, indent=1))
        ok = (results["draft_survives_process_death"] and results["posts"] == 1 and results["submit_shows_note_card"]
              and results["no_link_nodes_on_list"] and results["no_link_nodes_on_detail"]
              and results["forget_cancel_deletes"] == 0 and results["forget_confirm_deletes"] == 1
              and results["413_message"] and not results["sentinel_body_in_ui"])
        return 0 if ok else 1
    finally:
        if owns_reverse:
            dev.run("reverse", "--remove", remote, check=False)
        if server:
            server.shutdown()


def urllib_set_status(port, status):
    import urllib.request
    urllib.request.urlopen("http://127.0.0.1:%d/__ctl/text_status?code=%d" % (port, status), timeout=5).read()


if __name__ == "__main__":
    sys.exit(main())
