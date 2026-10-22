"""Adb-driven acceptance for text-note ingestion in the acceptance app (org.openlearning.app.acceptance).

Starts a loopback mock of the /api/v1 contract on this host (no database, no model),
forwards it with its own `adb reverse`, drives the already-installed acceptance app
through the UI and records uiautomator XML dumps only: this script saves no screenshots.
The process-death step presses HOME (`am kill` only kills a backgrounded app), which makes
Android take its own recents snapshot; the app sets FLAG_SECURE while the editor is shown, so
that snapshot is blank rather than a picture of the note. The mock logs method, path, status, body length and the
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
ALLOWED_PACKAGES = {PACKAGE, "com.google.android.inputmethod.latin", "com.android.systemui"}
FORBIDDEN_TEXT = re.compile(r"bitwarden|save password|autofill|use saved|password manager", re.I)
MIN_INPUT_GAP = 2.0  # seconds between any two input actions
MIN_POLL_GAP = 3.0   # seconds between uiautomator dumps
MAX_SCROLLS = 3      # bounded scroll attempts to reach an off-screen control
STEP = {"name": "start", "last_input": 0.0, "last_dump": 0.0}


class SupervisedAbort(RuntimeError):
    """An unexpected overlay or foreground change: stop touching the device immediately."""


def parse_bounds(raw):
    """'[x1,y1][x2,y2]' -> (x1, y1, x2, y2)."""
    x1, y1, x2, y2 = map(int, re.findall(r"-?\d+", raw))
    return x1, y1, x2, y2


def fully_visible(bounds, viewport):
    """True only when the whole node rectangle lies inside the viewport and is non-empty."""
    x1, y1, x2, y2 = bounds
    vx1, vy1, vx2, vy2 = viewport
    return x2 > x1 and y2 > y1 and x1 >= vx1 and y1 >= vy1 and x2 <= vx2 and y2 <= vy2


def app_viewport(root):
    """Visible app area: the app window's extent minus any input-method window covering its bottom."""
    # Window roots only (direct children of <hierarchy>): scrolled-off descendants have bounds outside the window.
    app = [parse_bounds(n.get("bounds")) for n in root.findall("node") if n.get("package") == PACKAGE and n.get("bounds")]
    if not app:
        raise SupervisedAbort("no app nodes to derive a viewport from")
    left, top = min(b[0] for b in app), min(b[1] for b in app)
    right, bottom = max(b[2] for b in app), max(b[3] for b in app)
    ime = [parse_bounds(n.get("bounds"))[1] for n in root.iter("node")
           if n.get("package") not in (None, PACKAGE) and "inputmethod" in n.get("package") and n.get("bounds")]
    if ime:
        bottom = min(bottom, min(ime))
    return left, top, right, bottom


def swipe_points(viewport):
    """Swipe-up gesture strictly inside the viewport (centre column, 75% -> 35% of its height)."""
    x1, y1, x2, y2 = viewport
    h = y2 - y1
    return (x1 + x2) // 2, y1 + h * 75 // 100, (x1 + x2) // 2, y1 + h * 35 // 100


def swipe_points_down(viewport):
    """Reverse gesture (finger moves down, content scrolls back toward the top), strictly inside the viewport."""
    sx, sy, ex, ey = swipe_points(viewport)
    return sx, ey, ex, sy


LINK_LABELS = ("Open original source", "Check source", "Open source article")
# Characters `adb shell input text` types verbatim and the device shell never reinterprets. ':', '/', '-', '_', '=',
# '@' and '+' cover the loopback URL and token; '%' is excluded because `input text` reads "%s" as a space.
SAFE_TEXT = re.compile(r"^[A-Za-z0-9 .,:/_=@+-]+$")


def body_digest(raw: bytes) -> dict:
    """What the mock may remember about a request body: its size and hash, never the content."""
    return {"len": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def expected_submit_bodies(title=NOTE_TITLE, text=NOTE_TEXT):
    """Candidate request bodies the app's Gson/Retrofit client should send, keyed by label variant.

    Gson (apiGson) writes compact JSON in field order title,text,label (or text,title), omits a null label and UTF-8 encodes it. The
    fixture text has no characters Gson HTML-escapes (< > & = '), so these bytes are exact unless the app's label
    handling differs; both an omitted and an empty label are therefore accepted. Gson's key order follows JVM
    reflection order, which is not contractual (observed on device: text before title), so both orders are accepted;
    the server parses JSON and does not care.
    """
    variants = {"label_omitted": {"title": title, "text": text}, "label_empty": {"title": title, "text": text, "label": ""},
                "label_omitted_text_first": {"text": text, "title": title},
                "label_empty_text_first": {"text": text, "title": title, "label": ""}}
    return {k: json.dumps(v, separators=(",", ":"), ensure_ascii=False).encode() for k, v in variants.items()}


def check_submit_digest(entry) -> dict:
    """Compare a logged POST digest (len + sha256) with the expected bodies; report which variant matched, if any."""
    for name, raw in expected_submit_bodies().items():
        d = body_digest(raw)
        if entry["len"] == d["len"] and entry["sha256"] == d["sha256"]:
            return {"submit_hash_match": True, "submit_hash_variant": name, "submit_len": entry["len"], "submit_sha256": entry["sha256"]}
    lens = {k: len(v) for k, v in expected_submit_bodies().items()}
    return {"submit_hash_match": False, "submit_len": entry["len"], "submit_sha256": entry["sha256"],
            "submit_len_matches_a_variant": entry["len"] in lens.values()}


def adb_text(text: str) -> str:
    """Encode text for `adb shell input text`; refuse anything the shell (or `input`) could reinterpret."""
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
        if args and args[0] in ("input", "am", "pm"):
            wait = STEP["last_input"] + MIN_INPUT_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            STEP["last_input"] = time.time()
        return self.run("shell", *args, check=check)

    def pid(self):
        return self.shell("pidof", PACKAGE, check=False).strip()

    def dump(self, name=None):
        wait = STEP["last_dump"] + MIN_POLL_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        raw = self.run("exec-out", "uiautomator", "dump", "/dev/tty")
        STEP["last_dump"] = time.time()
        raw = raw[raw.index("<?xml"): raw.rindex("</hierarchy>") + len("</hierarchy>")]
        root = ET.fromstring(raw)
        pkgs = {n.get("package") for n in root.iter("node")}
        if PACKAGE not in pkgs:
            raise SupervisedAbort("step %r: acceptance app not in foreground (packages %s)" % (STEP["name"], sorted(p for p in pkgs if p)))
        extra = {p for p in pkgs if p} - ALLOWED_PACKAGES
        hits = [t for n in root.iter("node") for t in (n.get("text"), n.get("content-desc")) if t and FORBIDDEN_TEXT.search(t)]
        if extra or hits:
            raise SupervisedAbort("step %r: unexpected overlay packages=%s texts=%s" % (STEP["name"], sorted(extra), hits[:3]))
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
            time.sleep(MIN_POLL_GAP)
        raise RuntimeError("UI element not found: " + pattern)

    def tap_scrolling(self, pattern, max_scrolls=MAX_SCROLLS):
        """Tap a node only when fully on screen; otherwise swipe inside the app window.

        Bounded: max_scrolls swipes toward the end of the list, then 2*max_scrolls back toward the top
        (the list can already be scrolled past the control, e.g. after opening and closing a detail view).
        """
        gestures = [swipe_points] * max_scrolls + [swipe_points_down] * (2 * max_scrolls)
        for attempt in range(len(gestures) + 1):
            root = self.dump()
            node = self.find(pattern, root)
            viewport = app_viewport(root)
            if node and fully_visible(parse_bounds(node["bounds"]), viewport):
                x1, y1, x2, y2 = parse_bounds(node["bounds"])
                self.shell("input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
                time.sleep(0.8)
                return
            if attempt == len(gestures):
                break
            sx, sy, ex, ey = gestures[attempt](viewport)
            self.shell("input", "swipe", str(sx), str(sy), str(ex), str(ey), "400")
            time.sleep(0.8)
        raise RuntimeError("UI element not reachable after %d scrolls: %s" % (len(gestures), pattern))

    def visible_scrolling(self, pattern, max_scrolls=MAX_SCROLLS):
        """True when a node matching pattern is fully on screen, swiping inside the app at most max_scrolls times.

        A LazyColumn composes only what is on screen, so a card below the fold is absent from the dump.
        """
        for attempt in range(max_scrolls + 1):
            root = self.dump()
            node = self.find(pattern, root)
            if node and fully_visible(parse_bounds(node["bounds"]), app_viewport(root)):
                return True
            if attempt == max_scrolls:
                return False
            sx, sy, ex, ey = swipe_points(app_viewport(root))
            self.shell("input", "swipe", str(sx), str(sy), str(ex), str(ey), "400")
            time.sleep(0.8)
        return False

    def type(self, text):
        self.shell("input", "text", adb_text(text))
        time.sleep(0.5)

    def wait_for(self, pattern, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if self.find(pattern):
                return True
            time.sleep(MIN_POLL_GAP)
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


def connect(dev, port, results):
    STEP["name"] = "connect:clear+launch"
    dev.shell("pm", "clear", PACKAGE)
    dev.shell("am", "start", "-n", ACTIVITY)
    dev.wait_for("Connect your server")
    dev.tap("Server URL")
    dev.type("http://127.0.0.1:%d" % port)
    STEP["name"] = "connect:tap Access token"
    dev.tap("Access token")
    STEP["name"] = "connect:type token"
    dev.type(TOKEN)
    time.sleep(4)
    STEP["name"] = "connect:post-token autofill check"
    dev.dump("00-after-token")  # raises SupervisedAbort if a Bitwarden/autofill sheet or foreign window is present
    results["bitwarden_prompt_at_token_step"] = False
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


def fill_editor(dev, open_editor=True):
    """Type the fixture note; open_editor=False when the caller already opened it (bounded scrolling)."""
    if open_editor:
        dev.tap("Write or import a note")
    dev.tap("Title")
    dev.type(NOTE_TITLE)
    dev.shell("input", "keyevent", "KEYCODE_BACK")
    dev.tap("Note text")
    dev.type(NOTE_TEXT)
    dev.shell("input", "keyevent", "KEYCODE_BACK")
    time.sleep(1.5)  # the draft is saved 400 ms after the last edit


def await_note_card(dev, results, timeout=40):
    """Wait (bounded) for the job row to read Completed, look for the card by scrolling, tap Refresh at most once if absent."""
    STEP["name"] = "submit:await completed"
    results["job_row_completed"] = dev.wait_for("Completed", timeout=timeout)
    STEP["name"] = "submit:find card"
    card = "Your note: " + NOTE_TITLE
    results["card_found_without_refresh"] = dev.visible_scrolling(card)
    results["refresh_tapped"] = False
    if results["card_found_without_refresh"]:
        return True
    STEP["name"] = "submit:refresh once"
    dev.tap_scrolling("^Refresh\\|")
    results["refresh_tapped"] = True
    time.sleep(MIN_POLL_GAP)
    return dev.visible_scrolling(card)


def mock_log():
    with LOCK:
        return list(STATE["log"])


def scenarios_pass(results) -> bool:
    return bool(results.get("draft_survives_process_death") and results.get("posts") == 1 and results.get("submit_hash_match") is not False
                and results.get("submit_shows_note_card") and results.get("no_link_nodes_on_list")
                and results.get("no_link_nodes_on_detail") and results.get("forget_cancel_deletes") == 0
                and results.get("forget_confirm_deletes") == 1 and results.get("413_message")
                and results.get("sentinel_body_in_ui") is False)


def write_evidence(out, results):
    """Always persist the request log and print partial results, even after an abort or crash."""
    try:
        (out / "request-log.json").write_text(json.dumps(mock_log(), indent=1))
    except OSError as exc:
        results["evidence_write_error"] = repr(exc)
    print(json.dumps(results, indent=1))


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
        connect(dev, args.port, results)

        fill_editor(dev)
        expected = len(NOTE_TEXT)
        dev.kill_and_relaunch()
        dev.wait_for("Note text|Write or import a note")
        counter = counter_value(dev.texts(dev.dump("01-draft-after-kill")))
        results["draft_survives_process_death"] = counter == expected
        results["draft_counter"] = counter

        dev.tap_scrolling("Add note to learning feed")
        results["submit_shows_note_card"] = await_note_card(dev, results)
        texts = dev.texts(dev.dump("02-after-submit"))
        log = mock_log()
        post = [e for e in log if e["method"] == "POST" and e["path"] == "/api/v1/ingest/text"]
        results["posts"] = len(post)
        if post:
            results.update(check_submit_digest(post[0]))
        results["no_link_nodes_on_list"] = not link_labels_present(texts)

        dev.tap_scrolling("Your note: " + NOTE_TITLE)
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

        STEP["name"] = "413:open editor"
        urllib_set_status(args.port, 413)
        dev.tap_scrolling("Write or import a note")
        fill_editor(dev, open_editor=False)
        dev.tap_scrolling("Add note to learning feed")
        results["413_message"] = dev.wait_for("larger than your server accepts", timeout=40)
        dev.dump("05-413")
        results["sentinel_body_in_ui"] = bool(dev.find("SENTINEL-BODY"))

        ok = scenarios_pass(results)
        return 0 if ok else 1
    except SupervisedAbort as exc:
        results["ABORTED"] = str(exc)
        results.setdefault("bitwarden_prompt_at_token_step", "token" in STEP["name"] or None)
        return 2
    except Exception as exc:  # evidence below still gets written; the device is left untouched
        results["CRASHED"] = "step %r: %r" % (STEP["name"], exc)
        return 3
    finally:
        write_evidence(out, results)
        if owns_reverse:
            dev.run("reverse", "--remove", remote, check=False)
        if server:
            server.shutdown()


def urllib_set_status(port, status):
    import urllib.request
    urllib.request.urlopen("http://127.0.0.1:%d/__ctl/text_status?code=%d" % (port, status), timeout=5).read()


if __name__ == "__main__":
    sys.exit(main())
