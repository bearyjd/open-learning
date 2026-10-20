"""Adb-driven reboot-recovery check for the acceptance app (org.openlearning.app.acceptance).

Reuses the mock server and device helpers of android_process_death.py and the
baseline/lock/crash helpers of android_fold.py. The host mock keeps running
across the reboot. The only device-changing action beyond the acceptance
package is ONE `adb reboot` (run it only with the device owner's consent): no
wm size/density, device_state, or setting is touched, and the script never
unlocks, locks, or guesses a PIN. After boot it waits (up to --unlock-timeout)
for the USER to unlock by hand, because credential-encrypted storage (Room DB,
Keystore-bound token) is unavailable until first unlock.

    adb install -r android/app/build/outputs/apk/acceptance/app-acceptance.apk
    python3 scripts/android_reboot.py --serial SERIAL [--port 18767]

Evidence is uiautomator XML only (no screenshots) under --out (default
.omc/reboot/, ignored by Git); the dumps are deleted when the script exits.
Results are printed as JSON lines; the exit code is 0 only if every check passed.
"""

import argparse
import json
import shutil
import signal
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import cast

from android_fold import check, crash_lines, device_baseline, locked, on_term
from android_process_death import (
    ACTIVITY,
    LOCK,
    PACKAGE,
    STATE,
    Device,
    connect,
    make_handler,
)

PROGRESS_EVERY_S = 30.0
POLL_GRACE_S = 200
IDEA_TITLE = "Spaced practice beats cramming"
QUERY = "spaced"


def say(message: str) -> None:
    print(message, flush=True)


def adb_quiet(dev: Device, *args: str) -> str:
    """Run adb tolerating a device that is offline/rebooting; '' on any failure."""
    try:
        return dev.run(*args, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return ""


def boot_id(dev: Device) -> str:
    return adb_quiet(dev, "shell", "cat", "/proc/sys/kernel/random/boot_id").strip()


def awake(dev: Device) -> bool:
    return "mWakefulness=Awake" in dev.shell("dumpsys power | grep -m1 mWakefulness=", check=False)


def user_unlocked(dev: Device) -> bool:
    return "RUNNING_UNLOCKED" in adb_quiet(dev, "shell", "am", "get-started-user-state", "0")


def job_polls(since: float) -> list[list[object]]:
    with LOCK:
        log = cast(list[list[object]], STATE["log"])
        return [e for e in log if "/jobs/" in str(e[2]) and cast(float, e[0]) >= since]


def wait_for_boot(dev: Device, old_boot_id: str, timeout_s: float) -> str:
    """Block until adb is back with a new boot id and sys.boot_completed=1."""
    start = time.time()
    last_report = start
    while time.time() - start < timeout_s:
        time.sleep(3)
        if time.time() - last_report >= PROGRESS_EVERY_S:
            last_report = time.time()
            say("waiting for adb/boot: %ds of %ds" % (last_report - start, timeout_s))
        new_id = boot_id(dev)
        done = adb_quiet(dev, "shell", "getprop", "sys.boot_completed").strip() == "1"
        if new_id and new_id != old_boot_id and done:
            return new_id
    raise RuntimeError("device did not come back on adb within %ds" % timeout_s)


def wait_for_unlock(dev: Device, timeout_s: float) -> bool:
    """Poll (read-only) until the user has unlocked; prints progress every 30 s."""
    start = time.time()
    last_report = start
    while time.time() - start < timeout_s:
        if user_unlocked(dev) and not locked(dev):
            return True
        if time.time() - last_report >= PROGRESS_EVERY_S:
            last_report = time.time()
            say("waiting for unlock: %ds of %ds" % (last_report - start, timeout_s))
        time.sleep(2)
    return False


def snapshot(dev: Device, label: str, out: Path) -> list[str]:
    root = dev.dump()
    (out / (label + ".xml")).write_text(ET.tostring(root, encoding="unicode"))
    return dev.texts(root)


def tab(dev: Device, name: str) -> list[str]:
    dev.tap(r"^%s\|" % name)
    time.sleep(1.5)
    return dev.texts()


def create_state(dev: Device, port: int) -> None:
    connect(dev, port)
    dev.tap(r"^Save\|")
    dev.wait_for("Saved")
    dev.tap("Article URL")
    dev.type("https://example.com/queued-article")
    dev.shell("input", "keyevent", "KEYCODE_BACK")
    time.sleep(1)
    dev.tap("Add to learning feed")
    if not dev.wait_for("Processing"):
        raise RuntimeError("queued import never showed Processing")
    dev.tap(r"^Explore\|")
    dev.tap("Search ideas")
    dev.type(QUERY)


def verify_after_unlock(dev: Device, out: Path, launched_at: float) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    landing = snapshot(dev, "10-landing", out)
    results.append(check("no reconnect screen (stored token decrypts)",
                         not any("Connect your server" in t for t in landing), landing[:8]))
    discover = tab(dev, "Discover")
    results.append(check("queued import listed, still Processing",
                         any("Processing" in t for t in discover), discover[:12]))
    with LOCK:
        STATE["job_done"] = True
    completed = False
    end = time.time() + POLL_GRACE_S + 60
    while time.time() < end and not completed:
        dev.tap(r"^Discover\|")
        completed = dev.wait_for("Completed", timeout=10)
    snapshot(dev, "11-discover-after-complete", out)
    results.append(check("import resumed and shows Completed after mock completes", completed,
                         len(job_polls(launched_at))))
    library = tab(dev, "Library")
    results.append(check("saved Library card present", any(IDEA_TITLE in t for t in library), library[:8]))
    explore = tab(dev, "Explore")
    snapshot(dev, "12-explore", out)
    results.append(check("Explore query lost (cold start, expected)",
                         not any(t == QUERY for t in explore), explore[:8]))
    # The boot-time "Unable to find <package>.test" line is the absent instrumentation package, not a crash.
    crashes = [line for line in crash_lines(dev) if PACKAGE + ".test" not in line]
    results.append(check("no app crash or ANR since boot", not crashes, crashes))
    return results


def before_reboot(dev: Device, port: int, remote: str, out: Path) -> str:
    """Prechecks, mock-backed state creation; returns the boot id to compare against."""
    if PACKAGE not in dev.shell("pm", "list", "packages", PACKAGE):
        raise RuntimeError("Install the acceptance APK first (see module docstring)")
    if locked(dev) or not awake(dev):
        raise RuntimeError("device must be unlocked and awake first (the script never unlocks)")
    if remote in dev.run("reverse", "--list"):
        raise RuntimeError("adb reverse already exists for " + remote + "; choose another --port")
    dev.run("reverse", "--no-rebind", remote, remote)
    create_state(dev, port)
    snapshot(dev, "00-before-reboot", out)
    old_boot = boot_id(dev)
    say("boot_id before: " + old_boot)
    return old_boot


def after_reboot(dev: Device, args: argparse.Namespace, out: Path, remote: str,
                 old_boot: str, since: float) -> list[dict[str, object]]:
    # adb itself only returns after the first unlock on this device, so one budget covers both.
    say("waiting for adb and the user's unlock (timeout %ds); the app is NOT started before then" % args.unlock_timeout)
    new_boot = wait_for_boot(dev, old_boot, args.unlock_timeout)
    say("boot_id after: %s (changed: %s)" % (new_boot, new_boot != old_boot))
    reverse_after = adb_quiet(dev, "reverse", "--list")
    say("adb reverse after boot: %r (mapping needed re-adding: %s)" % (reverse_after.strip(), remote not in reverse_after))
    dev.run("reverse", "--no-rebind", remote, remote)
    if not wait_for_unlock(dev, args.unlock_timeout):
        raise RuntimeError("unlock never happened within %ds" % args.unlock_timeout)
    say("unlock detected; observing WorkManager for %ds before launching the app" % POLL_GRACE_S)
    end = time.time() + POLL_GRACE_S
    while time.time() < end and not job_polls(since):
        time.sleep(3)
    before_launch = job_polls(since)
    say("job polls after reboot, before app launch: %s; app pid: %s"
        % (json.dumps(before_launch), dev.pid() or "-"))
    if not awake(dev):
        dev.shell("input", "keyevent", "KEYCODE_WAKEUP")
        time.sleep(1)
    launched_at = time.time()
    dev.shell("am", "start", "-n", ACTIVITY)
    time.sleep(5)
    results = verify_after_unlock(dev, out, launched_at)
    results.append(check("WorkManager polled before app launch", bool(before_launch), before_launch))
    return results


def run(dev: Device, args: argparse.Namespace, out: Path, remote: str) -> list[dict[str, object]]:
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        if args.resume_after_boot_id:
            # Device already rebooted in an earlier run whose mock was stopped; restore its fixture state.
            with LOCK:
                STATE["saved"] = True
            return after_reboot(dev, args, out, remote, args.resume_after_boot_id, time.time())
        old_boot = before_reboot(dev, args.port, remote, out)
        rebooted_at = time.time()
        say("REBOOTING NOW — unlock the phone when it boots")
        dev.run("reboot")
        return after_reboot(dev, args, out, remote, old_boot, rebooted_at)
    finally:
        server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--port", type=int, default=18767)
    parser.add_argument("--unlock-timeout", type=int, default=900)
    parser.add_argument("--resume-after-boot-id", default="",
                        help="skip setup and reboot; the device already rebooted from this boot id")
    parser.add_argument("--baseline", default="",
                        help="JSON baseline from the earlier run (needed with --resume-after-boot-id)")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / ".omc" / "reboot"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dev = Device(args.serial, out)
    remote = "tcp:%d" % args.port
    signal.signal(signal.SIGTERM, on_term)
    baseline = json.loads(args.baseline) if args.baseline else device_baseline(dev)
    say("baseline " + json.dumps(baseline))
    results: list[dict[str, object]] = []
    try:
        results = run(dev, args, out, remote)
    except RuntimeError as err:
        say("STOPPED: %s" % err)
    finally:
        adb_quiet(dev, "reverse", "--remove", remote)
        adb_quiet(dev, "uninstall", PACKAGE)
        shutil.rmtree(out, ignore_errors=True)
        try:
            final = device_baseline(dev)
            say("final " + json.dumps(final))
            say("restored to baseline: %s" % (final == baseline))
        except (RuntimeError, subprocess.TimeoutExpired) as err:
            say("could not read final device state: %s" % err)
    return 0 if results and all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
