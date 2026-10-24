# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in mock-backend discovery acceptance; NEVER runs on import.

Install the acceptance APK, then explicitly run:
    python3 scripts/android_discovery.py --serial SERIAL [--port 18767]

Clears only org.openlearning.app.acceptance, creates an owned loopback adb reverse,
and drives queue, feeds, topics and credential-scope clearing. No live backend,
feed provider, model, screenshots, logcat, reboot or device setting changes.
Reuses notes runner's overlay/foreground checks, input pacing and bounded scrolling.
Unexpected overlays stop input immediately. Evidence is private XML plus digest-only
request metadata and boolean verdicts in the git-ignored .omc/discovery directory.
No credentials, URLs, titles, topic terms or exception/server bodies are printed.
This runner is written and helper-tested; device execution is a separate user step.
"""

import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import android_notes as notes

TOKEN = notes.TOKEN
SECOND_TOKEN = "discovery-second-token"
FEED_URL = "https://example.invalid/discovery.xml"
PIN_TERM = "acceptance memory"
FEED_ID = "5eed0000-0000-4000-8000-000000000001"
ADDED_FEED_ID = "5eed0000-0000-4000-8000-000000000002"
NEWEST_ID = "5eed0000-0000-4000-8000-000000000011"
RELEVANT_ID = "5eed0000-0000-4000-8000-000000000012"
PIN_ID = "5eed0000-0000-4000-8000-000000000021"
STAMP = "2026-10-23T10:00:00Z"
SENTINEL = "SENTINEL-PRIVATE-SERVER-BODY"
REQUIRED_RESULTS = (
    "newest", "pagination", "relevance", "ranking_restart", "ingest", "dismiss",
    "feed_add", "feed_remove_cancel", "feed_remove", "refresh_429", "pin", "unpin", "scope_cleared",
    "fixed_errors",
)


def feed(ident=FEED_ID):
    return {"id": ident, "url": FEED_URL, "title": "Acceptance feed", "created_at": STAMP,
            "last_fetched_at": STAMP, "last_error": None, "consecutive_failures": 0, "paused": False}


def item(ident):
    newest = ident == NEWEST_ID
    return {"id": ident, "feed_id": FEED_ID, "url": "https://example.invalid/" + ident,
            "title": "Newest acceptance article" if newest else "Relevant acceptance article",
            "snippet": "Private discovery fixture.", "published_at": STAMP, "created_at": STAMP,
            "status": "new", "score": 0.0 if newest else 10.0,
            "matched_topics": [] if newest else ["memory"]}


class MockBackend:
    """Stateful API fixture. Stores request digests, never raw bodies or query values."""

    def __init__(self):
        self.log = []
        self.lock = threading.Lock()
        self.remaining = {NEWEST_ID, RELEVANT_ID}
        self.added = False
        self.pin = False
        self.stale_once = True
        self.rate_once = True

    def request(self, method, target, raw=b"", token=None):
        with self.lock:
            parsed = urlsplit(target)
            path, query = parsed.path, parse_qs(parsed.query)
            if token not in (TOKEN, SECOND_TOKEN):
                response = (401, {"detail": SENTINEL}, {})
            else:
                try:
                    payload = json.loads(raw) if raw else {}
                except (ValueError, UnicodeError):
                    payload = None
                response = self._route(method, path, query, payload, token == SECOND_TOKEN)
            code, body, headers = response
            self.log.append({"method": method, "path": self._safe_path(path), "status": code,
                             **notes.body_digest(raw)})
            return code, body, {"Cache-Control": "no-store", **headers}

    @staticmethod
    def _safe_path(path):
        # Only fixture routes/IDs enter evidence, including for unexpected requests.
        allowed = {"/api/v1/" + name for name in ("discover", "feeds", "topics", "topics/pinned",
                   "feed/today", "library", "notes", "limits", "jobs", "jobs/discovery-job")}
        allowed |= {f"/api/v1/discover/{ident}/{action}" for ident in (NEWEST_ID, RELEVANT_ID)
                    for action in ("ingest", "dismiss")}
        allowed |= {f"/api/v1/feeds/{ident}{suffix}" for ident in (FEED_ID, ADDED_FEED_ID)
                    for suffix in ("", "/refresh")}
        allowed.add("/api/v1/topics/pinned/" + PIN_ID)
        return path if path in allowed else "<unrecognized>"

    def _route(self, method, path, query, payload, second_scope):
        key = (method, path)
        if key == ("GET", "/api/v1/discover"):
            relevance = query.get("sort", ["newest"])[0] == "relevance"
            cursor = query.get("cursor")
            if relevance and cursor and self.stale_once and not second_scope:
                self.stale_once = False
                return 409, {"detail": SENTINEL}, {}
            order = [RELEVANT_ID, NEWEST_ID] if relevance else [NEWEST_ID, RELEVANT_ID]
            rows = [item(ident) for ident in order if ident in self.remaining and not second_scope]
            if cursor:
                rows = rows[1:]
            return 200, {"items": rows[:1], "next_cursor": "mock-next" if len(rows) > 1 else None}, {}
        if key == ("GET", "/api/v1/feeds"):
            rows = ([feed(ADDED_FEED_ID)] if self.added else []) + [feed()]
            return 200, [] if second_scope else rows, {}
        if key == ("POST", "/api/v1/feeds"):
            if not isinstance(payload, dict) or payload.get("url") != FEED_URL:
                return 422, {"detail": SENTINEL}, {}
            if self.added:
                return 409, {"detail": SENTINEL}, {}
            self.added = True
            return 201, feed(ADDED_FEED_ID), {}
        if method == "DELETE" and path == "/api/v1/feeds/" + ADDED_FEED_ID:
            self.added = False
            return 204, None, {}
        if method == "POST" and path in {f"/api/v1/feeds/{ident}/refresh" for ident in (FEED_ID, ADDED_FEED_ID)}:
            if self.rate_once:
                self.rate_once = False
                return 429, {"detail": SENTINEL}, {"Retry-After": "1"}
            return 200, feed(path.split("/")[-2]), {}
        if key == ("GET", "/api/v1/topics"):
            return 200, {"topics": [] if second_scope else [{"term": "memory", "weight": 2.0, "count": 2}],
                         "pinned": [self._pin()] if self.pin and not second_scope else []}, {}
        if key == ("POST", "/api/v1/topics/pinned"):
            if not isinstance(payload, dict) or payload.get("term") != PIN_TERM:
                return 422, {"detail": SENTINEL}, {}
            self.pin = True
            return 201, self._pin(), {}
        if key == ("DELETE", "/api/v1/topics/pinned/" + PIN_ID):
            self.pin = False
            return 204, None, {}
        for ident in (NEWEST_ID, RELEVANT_ID):
            for action in ("ingest", "dismiss"):
                if key == ("POST", f"/api/v1/discover/{ident}/{action}"):
                    if ident not in self.remaining:
                        return 409, {"detail": SENTINEL}, {}
                    self.remaining.remove(ident)
                    return ((202, self._job(), {}) if action == "ingest" else (204, None, {}))
        if key == ("GET", "/api/v1/jobs/discovery-job"):
            return 200, self._job(), {}
        if method == "GET" and path in {"/api/v1/feed/today", "/api/v1/library", "/api/v1/jobs", "/api/v1/notes"}:
            return 200, [], {}
        if key == ("GET", "/api/v1/limits"):
            return 200, {"max_document_chars": 60000, "min_text_chars": 100,
                         "max_title_chars": 200, "max_label_chars": 80}, {}
        return 404, {"detail": SENTINEL}, {}

    @staticmethod
    def _pin():
        return {"id": PIN_ID, "term": PIN_TERM, "created_at": STAMP}

    @staticmethod
    def _job():
        return {"id": "discovery-job", "status": "completed", "error": None, "document_id": None}

    def snapshot(self):
        with self.lock:
            return list(self.log)


def make_handler(mock):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def handle_request(self):
            raw = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 65536))
            auth = self.headers.get("Authorization", "")
            code, body, headers = mock.request(self.command, self.path, raw,
                                               auth[7:] if auth.startswith("Bearer ") else None)
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_DELETE = handle_request
    return Handler


class Device(notes.Device):
    def shell(self, *args, check=True):
        if args and args[0] == "input":
            return self.run("shell", *args, check=check)
        return super().shell(*args, check=check)

    @staticmethod
    def ordinary_bar_geometry(block, status_bar):
        rectangle = r"(\[-?\d+,-?\d+\]\[-?\d+,-?\d+\])"
        frames = re.findall(r"(?m)^\s*Frames:([^\n]+)$", block)
        if frames:
            if len(frames) != 1:
                return False
            display = re.findall(r"\bdisplay=" + rectangle, frames[0])
            frame = re.findall(r"\bframe=" + rectangle, frames[0])
        else:
            display = re.findall(r"\bmDisplayFrame=" + rectangle, block)
            frame = re.findall(r"\bmFrame=" + rectangle, block)
        if len(display) != 1 or len(frame) != 1:
            return False
        dx1, dy1, dx2, dy2 = map(int, re.findall(r"-?\d+", display[0]))
        x1, y1, x2, y2 = map(int, re.findall(r"-?\d+", frame[0]))
        if not (dx1 <= x1 < x2 <= dx2 and dy1 <= y1 < y2 <= dy2):
            return False
        # A bar must occupy a full display edge and at most a thin strip of it.
        thickness = min(160, min(dx2 - dx1, dy2 - dy1) // 10)
        horizontal = x1 == dx1 and x2 == dx2 and y2 - y1 <= thickness
        if status_bar:
            return horizontal and y1 == dy1
        vertical = y1 == dy1 and y2 == dy2 and x2 - x1 <= thickness
        return ((horizontal and y2 == dy2)
                or (vertical and (x1 == dx1 or x2 == dx2)))

    def check_input_window(self):
        """Fail closed on stale XML: inspect actual windows just before each input."""
        try:
            windows = self.run("shell", "dumpsys", "window", "windows")
            focus_lines = re.findall(r"(?m)^\s*mCurrentFocus=(.*)$", windows)
            focus = re.fullmatch(r"Window\{([^}\n]+)\}", focus_lines[0]) if len(focus_lines) == 1 else None
            if not focus or not re.search(
                    r"\s" + re.escape(notes.PACKAGE) + r"(?:/|\s|$)", focus[1]):
                raise ValueError("unknown focus")
            blocks = re.split(r"(?m)^\s*Window #\d+ Window\{", windows)[1:]
            focused_visible = False
            for block in blocks:
                owner = re.search(r"(?m)^\s*mOwnerUid=\d+\b[^\n]*\bpackage=([^\s]+)", block)
                visibility = re.findall(r"\b(?:isOnScreen|isVisible|isReadyForDisplay\(\))=(true|false)\b", block)
                if not owner or not visibility:
                    raise ValueError("unknown window")
                if "true" not in visibility:
                    continue
                if owner[1] != notes.PACKAGE:
                    # Ordinary system bars are chrome; sheets, dialogs and IMEs are not.
                    title = re.match(r"\S+ u\d+ (StatusBar|NavigationBar\d*)\}:", block)
                    kind = re.search(r"\bmAttrs=\{[^\n]*\bty=(STATUS_BAR|NAVIGATION_BAR)\b", block)
                    if (owner[1] != "com.android.systemui" or not title or not kind
                            or (title[1] == "StatusBar") != (kind[1] == "STATUS_BAR")
                            or not self.ordinary_bar_geometry(block, kind[1] == "STATUS_BAR")):
                        raise ValueError("foreign window")
                if block.startswith(focus[1] + "}"):
                    focused_visible = True
            if not focused_visible:
                raise ValueError("focused window missing or hidden")
        except Exception:
            raise notes.SupervisedAbort("unexpected foreground or overlay; input stopped") from None

    def run(self, *args, check=True):
        if args[:3] == ("shell", "input", "keyevent") and len(args) > 4:
            # adb accepts several keys in one command; each is a separate input event.
            return "".join(self.run("shell", "input", "keyevent", key, check=check) for key in args[3:])
        if args[:2] == ("shell", "input"):
            wait = notes.STEP["last_input"] + notes.MIN_INPUT_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            notes.STEP["last_input"] = time.time()
            # Every entry point paces first, then makes this final read before input.
            self.check_input_window()
        # Parent failures include command arguments/stderr; those may contain credentials.
        try:
            return super().run(*args, check=check)
        except Exception:
            raise RuntimeError("adb operation failed") from None

    def dump(self, name=None):
        try:
            return super().dump(name)
        except notes.SupervisedAbort:
            raise notes.SupervisedAbort("unexpected foreground or overlay; input stopped") from None


def scenarios_pass(results):
    return (not results.get("aborted") and not results.get("runner_failed")
            and all(results.get(key) is True for key in REQUIRED_RESULTS))


def saw(mock, method, path, status):
    return any(e["method"] == method and e["path"] == path and e["status"] == status for e in mock.snapshot())


def wait_request(mock, method, path, status, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if saw(mock, method, path, status):
            return True
        time.sleep(1)
    return False


def run_scenarios(dev, mock, results):
    notes.STEP["name"] = "queue"
    dev.tap_scrolling("Reading queue")
    results["newest"] = dev.wait_for("Newest acceptance article")
    dev.tap_scrolling(r"^Load more\|")
    results["pagination"] = dev.visible_scrolling("Relevant acceptance article")
    dev.tap_scrolling(r"^Relevance\|")
    results["relevance"] = dev.wait_for("Relevant acceptance article")
    dev.tap_scrolling(r"^Load more\|")
    results["ranking_restart"] = (wait_request(mock, "GET", "/api/v1/discover", 409)
                                  and dev.wait_for("Your topics changed. The reading queue has been refreshed.")
                                  and dev.wait_for("Relevant acceptance article"))
    dev.tap_scrolling(r"^Newest\|")
    dev.tap_scrolling(r"^Ingest\|")
    results["ingest"] = (wait_request(mock, "POST", f"/api/v1/discover/{NEWEST_ID}/ingest", 202)
                         and dev.wait_for("Completed"))
    # Job tracking can return to Discover; reopen queue if ingestion navigated there.
    if dev.find("Write or import a note"):
        dev.tap_scrolling("Reading queue")
    dev.tap_scrolling(r"^Refresh\|")
    if not dev.wait_for("Relevant acceptance article") or dev.find("Newest acceptance article"):
        results["ingest"] = False
    dev.tap_scrolling(r"^Dismiss\|")
    results["dismiss"] = (wait_request(mock, "POST", f"/api/v1/discover/{RELEVANT_ID}/dismiss", 204)
                          and dev.wait_for("Your reading queue is empty"))
    dev.dump("01-queue")

    notes.STEP["name"] = "feeds"
    dev.tap_scrolling(r"^Feeds\|")
    dev.tap_scrolling("Feed URL")
    dev.type(FEED_URL)
    dev.shell("input", "keyevent", "KEYCODE_BACK")
    dev.tap_scrolling(r"^Add feed\|")
    results["feed_add"] = wait_request(mock, "POST", "/api/v1/feeds", 201)
    dev.tap_scrolling(r"^Refresh now\|")
    refresh = f"/api/v1/feeds/{ADDED_FEED_ID}/refresh"
    limited = wait_request(mock, "POST", refresh, 429)
    results["fixed_errors"] = (limited and dev.wait_for("The server is busy. Wait before trying again.", timeout=10)
                               and not dev.find(SENTINEL))
    # Depending on the view model, Retry-After enables a manual retry or retries automatically.
    time.sleep(3)
    if not saw(mock, "POST", refresh, 200):
        dev.tap_scrolling(r"^Refresh now\|")
    results["refresh_429"] = limited and wait_request(mock, "POST", refresh, 200)
    dev.tap_scrolling(r"^Remove\|")
    dev.tap_last(r"^Cancel\|")
    results["feed_remove_cancel"] = not saw(mock, "DELETE", f"/api/v1/feeds/{ADDED_FEED_ID}", 204)
    dev.tap_scrolling(r"^Remove\|")
    dev.tap_last(r"^Remove\|")
    results["feed_remove"] = wait_request(mock, "DELETE", f"/api/v1/feeds/{ADDED_FEED_ID}", 204)
    dev.dump("02-feeds")
    notes.STEP["name"] = "topics"
    dev.tap_scrolling(r"^Topics\|")
    dev.tap_scrolling("Topic term")
    dev.type(PIN_TERM)
    dev.shell("input", "keyevent", "KEYCODE_BACK")
    dev.tap_scrolling(r"^Pin topic\|")
    results["pin"] = dev.wait_for(PIN_TERM) and wait_request(mock, "POST", "/api/v1/topics/pinned", 201)
    dev.tap_scrolling(r"^Unpin\|")
    results["unpin"] = wait_request(mock, "DELETE", "/api/v1/topics/pinned/" + PIN_ID, 204)
    dev.dump("03-topics")
    # Repopulate the fixture so an empty queue in the next scope proves clearing.
    with mock.lock:
        mock.remaining = {NEWEST_ID, RELEVANT_ID}
    # A full close clears accepted/dismissed-item tombstones; subsection changes retain them.
    dev.tap_scrolling(r"^Close\|")
    dev.tap_scrolling(r"^Reading queue\|")
    if not dev.wait_for("Newest acceptance article"):
        raise RuntimeError("scope fixture unavailable")
    dev.tap_scrolling(r"^Topics\|")
    if not dev.wait_for(r"^memory\|"):
        raise RuntimeError("topic scope fixture unavailable")
    # Leave a private unsent term in the field: ordinary remember must be disposed too.
    dev.tap_scrolling("Topic term")
    dev.type(PIN_TERM)
    dev.shell("input", "keyevent", "KEYCODE_BACK")
    dev.tap("Connection settings")
    dev.tap("Access token")
    dev.shell("input", "keyevent", "KEYCODE_MOVE_END")
    dev.shell("input", "keyevent", *(["KEYCODE_DEL"] * len(TOKEN)))
    dev.type(SECOND_TOKEN)
    dev.shell("input", "keyevent", "KEYCODE_BACK")
    dev.tap_last(r"^Connect\|")
    # Configure may preserve or close the private route; use its common entry label.
    dev.tap_scrolling(r"^Reading queue\|")
    queue_clear = dev.wait_for("Your reading queue is empty") and not dev.find("Newest acceptance article|Relevant acceptance article")
    dev.tap_scrolling(r"^Feeds\|")
    feeds_clear = dev.wait_for("No feeds yet") and not dev.find("Acceptance feed")
    dev.tap_scrolling(r"^Topics\|")
    topics_clear = dev.wait_for("No derived topics yet") and not dev.find(r"^memory\||" + re.escape(PIN_TERM))
    results["scope_cleared"] = queue_clear and feeds_clear and topics_clear
    dev.dump("04-new-scope")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--port", type=int, default=18767)
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / ".omc" / "discovery"))
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dev, mock = Device(args.serial, out), MockBackend()
    server, owns_reverse, results = None, False, {}
    remote = f"tcp:{args.port}"
    try:
        if notes.PACKAGE not in dev.shell("pm", "list", "packages", notes.PACKAGE):
            raise RuntimeError("acceptance package missing")
        if remote in dev.run("reverse", "--list"):
            raise RuntimeError("reverse already exists")
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(mock))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        dev.run("reverse", "--no-rebind", remote, remote)
        owns_reverse = True
        notes.connect(dev, args.port, results)
        run_scenarios(dev, mock, results)
        return 0 if scenarios_pass(results) else 1
    except notes.SupervisedAbort:
        results["aborted"] = True
        return 2
    except Exception:
        results["runner_failed"] = True
        return 3
    finally:
        (out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        (out / "requests.json").write_text(json.dumps(mock.snapshot(), indent=2) + "\n")
        if owns_reverse:
            dev.run("reverse", "--remove", remote, check=False)
        if server:
            server.shutdown()
        print("Discovery acceptance:", "PASS" if scenarios_pass(results) else "FAIL")


if __name__ == "__main__":
    sys.exit(main())
