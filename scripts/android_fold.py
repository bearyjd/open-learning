"""Adb-driven fold/unfold check for the acceptance app (org.openlearning.app.acceptance).

Reuses the mock server and device helpers of android_process_death.py. The
posture is emulated with `cmd device_state state N`; that is NOT a physical
hinge test. Only device_state is changed: no reboot, wm size/density, or
setting is touched, and only the acceptance package is used. `device_state
state reset` always runs in `finally` (also on Ctrl-C/SIGTERM), after which the
script compares device_state, wm size/density and the reverse list with the
baseline it recorded first.

    adb install -r android/app/build/outputs/apk/acceptance/app-acceptance.apk
    python3 scripts/android_fold.py --serial SERIAL [--port 18766]

Evidence is uiautomator XML only (no screenshots) under --out (default
.omc/fold/, ignored by Git). Pass/fail for each check is printed as JSON.
"""

import argparse
import json
import re
import signal
import sys
import threading
import time
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer
from pathlib import Path

from android_process_death import PACKAGE, Device, connect, make_handler

TABS = ("Discover", "Library", "Learn", "Review", "Explore")
BREAKPOINT_DP = 840


def on_term(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def device_baseline(dev: Device) -> dict[str, str]:
    return {
        "size": dev.shell("wm", "size").strip(),
        "density": dev.shell("wm", "density").strip(),
        "state": dev.shell("cmd", "device_state", "state").strip(),
        "reverse": dev.run("reverse", "--list").strip(),
    }


def state_ids(dev: Device) -> dict[str, int]:
    found = re.findall(
        r"identifier=(\d+), name='(\w+)'",
        dev.shell("cmd", "device_state", "print-states"),
    )
    return {name: int(ident) for ident, name in found}


def window_dp(dev: Device) -> str:
    """Width/height dp from the app's resolved configuration (only our line leaves the device)."""
    script = (
        "dumpsys activity activities | grep -A40 'Hist  #0: ActivityRecord.*%s' | grep -m1 -o -E 'w[0-9]+dp h[0-9]+dp'"
        % PACKAGE
    )
    return dev.shell(script, check=False).strip() or "unknown"


def lifecycle_count(dev: Device) -> int:
    """Count of MainActivity onCreate/relaunch events (read-only events log; not cleared)."""
    events = dev.run("logcat", "-b", "events", "-d")
    pattern = re.compile(
        r"wm_on_create_called.*MainActivity|wm_relaunch.*" + re.escape(PACKAGE)
    )
    return sum(1 for line in events.splitlines() if pattern.search(line))


def locked(dev: Device) -> bool:
    return dev.shell(
        "dumpsys window | grep -c 'isKeyguardShowing=true'", check=False
    ).strip() not in ("", "0")


def crash_lines(dev: Device) -> list[str]:
    log = dev.run("logcat", "-d", "-s", "AndroidRuntime:E", "ActivityManager:E")
    events = dev.run("logcat", "-b", "events", "-d")
    hits = [line for line in log.splitlines() if PACKAGE in line]
    return hits + [
        line for line in events.splitlines() if "am_anr" in line and PACKAGE in line
    ]


def center(bounds: str) -> tuple[int, int]:
    x1, y1, x2, y2 = map(int, re.findall(r"\d+", bounds))
    return (x1 + x2) // 2, (y1 + y2) // 2


def contains(bounds: str, x: int, y: int) -> bool:
    x1, y1, x2, y2 = map(int, re.findall(r"\d+", bounds))
    return x1 <= x <= x2 and y1 <= y <= y2


def observe(dev: Device, label: str, out: Path) -> dict[str, object]:
    """One uiautomator snapshot: bounds, nav layout, active tab and selected labels."""
    root = dev.dump()
    (out / (label + ".xml")).write_text(ET.tostring(root, encoding="unicode"))
    nodes = [n.attrib for n in root.iter("node") if n.get("package") == PACKAGE]
    frame = nodes[0]["bounds"] if nodes else "none"
    tabs = [n for n in nodes if n.get("text") in TABS]
    centres = {n["text"]: center(n["bounds"]) for n in tabs}
    xs = {round(c[0] / 20) for c in centres.values()}
    ys = {round(c[1] / 20) for c in centres.values()}
    nav = (
        "none"
        if len(centres) < len(TABS)
        else "rail"
        if len(xs) == 1 and len(ys) > 1
        else "bar"
        if len(ys) == 1
        else "mixed"
    )
    selected = [n["bounds"] for n in nodes if n.get("selected") == "true"]
    active = [
        t for t, (x, y) in centres.items() if any(contains(b, x, y) for b in selected)
    ]
    return {
        "label": label,
        "bounds": frame,
        "dp": window_dp(dev),
        "nav": nav,
        "active": sorted(set(active)),
        "texts": dev.texts(root),
    }


def posture(dev: Device, state: int, settle: float = 4.0) -> None:
    dev.shell("cmd", "device_state", "state", str(state))
    time.sleep(settle)
    dev.shell(
        "input", "keyevent", "KEYCODE_WAKEUP"
    )  # a closed posture may sleep the display
    time.sleep(1)


def has(snap: dict[str, object], pattern: str) -> bool:
    return any(re.search(pattern, t) for t in snap["texts"])  # type: ignore[attr-defined]


def check(name: str, ok: bool, detail: object) -> dict[str, object]:
    print(json.dumps({"check": name, "ok": ok, "detail": detail}))
    return {"check": name, "ok": ok}


def run_checks(dev: Device, ids: dict[str, int], out: Path) -> list[dict[str, object]]:
    folded, opened, half = ids["CLOSED"], ids["OPENED"], ids["HALF_OPENED"]
    results: list[dict[str, object]] = []
    creates = lifecycle_count(dev)

    def recreated(name: str, expect: bool) -> None:
        nonlocal creates
        now = lifecycle_count(dev)
        results.append(
            check(
                "onCreate/relaunch events: " + name,
                (now > creates) == expect,
                [creates, now],
            )
        )
        creates = now

    # 1. Folded: Explore query typed and Connection settings text typed, then unfold.
    dev.tap(r"^Explore\|")
    dev.tap("Search ideas")
    dev.type("spaced")
    dev.shell("input", "keyevent", "KEYCODE_BACK")
    time.sleep(1)
    folded_snap = observe(dev, "01-folded-explore", out)
    dev.tap("Connection settings")
    dev.tap("Server URL")
    dev.type("zzz")
    typed = observe(dev, "02-folded-settings-typed", out)
    posture(dev, opened)
    open_snap = observe(dev, "03-unfolded", out)
    print(
        json.dumps(
            {
                "folded": [
                    folded_snap["bounds"],
                    folded_snap["dp"],
                    folded_snap["nav"],
                ],
                "unfolded": [open_snap["bounds"], open_snap["dp"], open_snap["nav"]],
            }
        )
    )
    recreated("fold->unfold", True)
    results.append(
        check("folded uses bottom bar", folded_snap["nav"] == "bar", folded_snap["dp"])
    )
    results.append(
        check("unfolded uses rail", open_snap["nav"] == "rail", open_snap["dp"])
    )
    results.append(
        check(
            "explore destination kept",
            open_snap["active"] == ["Explore"],
            open_snap["active"],
        )
    )
    results.append(
        check("explore query kept", has(open_snap, "spaced"), "text 'spaced' present")
    )
    results.append(
        check(
            "settings dialog still open",
            has(open_snap, "Connect your server"),
            "dialog title",
        )
    )
    results.append(
        check(
            "unsaved settings text lost (expected)",
            has(typed, "zzz") and not has(open_snap, "zzz"),
            [has(typed, "zzz"), has(open_snap, "zzz")],
        )
    )
    for _ in range(3):
        if dev.find("Connect your server"):
            dev.shell("input", "keyevent", "KEYCODE_BACK")
            time.sleep(1)

    # 2. Unfolded: tutor draft, then HALF_OPENED (still the inner display) and back to OPENED.
    dev.tap(r"^Learn\|")
    dev.tap("Start learning")
    dev.wait_for("Explain it in your own words")
    dev.tap("Explain it in your own words")
    dev.type("fold draft")
    dev.shell("input", "keyevent", "KEYCODE_BACK")
    time.sleep(1)
    for name, state in (("half-opened", half), ("opened again", opened)):
        posture(dev, state)
        snap = observe(dev, "04-" + name.replace(" ", "-"), out)
        print(json.dumps({name: [snap["bounds"], snap["dp"], snap["nav"]]}))
        recreated("-> " + name, False)
        results.append(
            check(
                "tutor draft kept at " + name, has(snap, "fold draft"), snap["active"]
            )
        )

    # 3. Fold closed: the device locks, so nothing after this can be read without the user.
    posture(dev, folded)
    results.append(
        check(
            "fold-closed locks the device (not verifiable)",
            locked(dev),
            "keyguard showing",
        )
    )
    results.append(check("no app crash or ANR", not crash_lines(dev), crash_lines(dev)))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument(
        "--out", default=str(Path(__file__).resolve().parents[1] / ".omc" / "fold")
    )
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dev = Device(args.serial, out)
    remote = "tcp:%d" % args.port
    signal.signal(signal.SIGTERM, on_term)
    baseline = device_baseline(dev)
    print("baseline", json.dumps(baseline))
    owns_reverse = False
    server = None
    results: list[dict[str, object]] = []
    try:
        if PACKAGE not in dev.shell("pm", "list", "packages", PACKAGE):
            raise RuntimeError(
                "Install the acceptance APK first (see module docstring)"
            )
        if locked(dev):
            raise RuntimeError("device is locked; unlock it first (the script never unlocks)")
        ids = state_ids(dev)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        dev.run("reverse", "--no-rebind", remote, remote)
        owns_reverse = True
        connect(dev, args.port)
        dev.tap(r"^Save\|")  # the Learn list needs a saved idea
        dev.wait_for("Saved")
        results = run_checks(dev, ids, out)
    finally:
        dev.shell("cmd", "device_state", "state", "reset", check=False)
        time.sleep(3)
        dev.shell("input", "keyevent", "KEYCODE_WAKEUP", check=False)
        if owns_reverse:
            dev.run("reverse", "--remove", remote, check=False)
        if server:
            server.shutdown()
        final = device_baseline(dev)
        print("final", json.dumps(final))
        print("restored to baseline:", final == baseline)
    return 0 if results and all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
