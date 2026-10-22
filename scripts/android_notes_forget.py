"""Offline-forget scenario for android_notes.py (E2b-3), split out to keep that script small.

Mock offline -> forget a listed note -> the list shows "N forgotten note(s) waiting for the server" and the mock has seen
no DELETE for it -> mock online -> within a bounded wait the mock sees exactly one DELETE for that document and the
waiting line disappears. Pure helpers take the mock log (method/path/status only) and uiautomator texts; the scenario
drives the device only through the Device methods that already throttle input and dumps. Not run by the unit tests.
"""

import re
import time

DOC = "list-doc-b"
TITLE = "Beta list note"
WAITING = re.compile(r"(\d+) forgotten notes? waiting for the server")
REPLAY_TIMEOUT = 150  # seconds: the first failed attempt backs off 30 s, the next one may back off 60 s
POLL_GAP = 3.0        # mirrors MIN_POLL_GAP in android_notes.py


def deletes_for(log, doc=DOC) -> int:
    return sum(1 for e in log if e["method"] == "DELETE" and e["path"] == "/api/v1/notes/" + doc)


def waiting_count(texts):
    """The N in the list's waiting line, or None when the line is not shown."""
    for text in texts:
        match = WAITING.search(text)
        if match:
            return int(match.group(1))
    return None


def offline_summary(log, texts) -> dict:
    """After the offline forget: one waiting note, the title gone from the list, and no DELETE has reached the mock."""
    return {"offline_waiting_count": waiting_count(texts), "offline_deletes": deletes_for(log),
            "offline_row_hidden": not any(TITLE in t for t in texts)}


def online_summary(log, texts) -> dict:
    """After reconnecting: exactly one DELETE for the document and no waiting line."""
    return {"online_deletes": deletes_for(log), "online_waiting_gone": waiting_count(texts) is None}


def forget_scenario_passes(results) -> bool:
    return bool(results.get("offline_waiting_count") == 1 and results.get("offline_deletes") == 0
                and results.get("offline_row_hidden") and results.get("online_deletes") == 1
                and results.get("online_waiting_gone"))


def run_offline_forget(dev, set_offline, mock_log, step, results):
    """Starts and ends on Discover. [set_offline] flips the mock; [step] records the current step name for crash evidence."""
    step["name"] = "offline-forget:open list"
    dev.tap_scrolling("Your notes")
    results["offline_list_loaded"] = dev.wait_for(TITLE)
    set_offline(True)
    step["name"] = "offline-forget:forget"
    dev.tap(r"^Forget\|")
    dev.wait_for("Forget this note")
    dev.tap_last(r"^Forget\|")
    dev.wait_for("waiting for the server")
    results.update(offline_summary(mock_log(), dev.texts(dev.dump("04d-offline-forget"))))
    step["name"] = "offline-forget:reconnect"
    set_offline(False)
    end = time.time() + REPLAY_TIMEOUT
    while time.time() < end and deletes_for(mock_log()) < 1:
        time.sleep(POLL_GAP)
    dev.wait_for(r"^Alpha|Your notes", timeout=6)
    end = time.time() + 30
    texts = dev.texts(dev.dump("04e-offline-forget-replayed"))
    while time.time() < end and waiting_count(texts) is not None:
        time.sleep(POLL_GAP)
        texts = dev.texts(dev.dump())
    results.update(online_summary(mock_log(), texts))
    dev.tap(r"^Close\|")
    dev.wait_for("Write or import a note")
