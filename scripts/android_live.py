"""Run the opt-in Android UI test against a fresh, disposable live backend.

No APK builds, installs, backend resets, paid-service changes, or API writes.
Credentials travel in adb stdin to the acceptance sandbox, never arguments.
Requires OL_API_TOKEN, OL_LIVE_ISOLATED=1, a running backend/worker/model, and
installed acceptance app/test APKs. See android/README.md for the exact command.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PACKAGE = "org.openlearning.app.acceptance"
INSTRUMENTATION = PACKAGE + ".test/androidx.test.runner.AndroidJUnitRunner"
CLASS = "org.openlearning.app.LiveLearningLoopTest"
CONFIG = "files/live-learning-loop.json"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Allow article ingestion, save, two model turns, and a review rating",
    )
    parser.add_argument(
        "--serial", help="Select an adb device (required if more than one is attached)"
    )
    args = parser.parse_args()
    stage = "configuration"
    adb_prefix = None
    owns_reverse = False
    seeded = False
    remote = None
    result_code = 1
    token = os.environ.get("OL_API_TOKEN", "")
    child_env = {
        key: value for key, value in os.environ.items() if key != "OL_API_TOKEN"
    }

    def adb(*arguments, data=None, timeout=30):
        result = subprocess.run(
            [*adb_prefix, *arguments],
            input=data,
            capture_output=True,
            timeout=timeout,
            env=child_env,
            check=False,
        )
        require(
            result.returncode == 0,
            "adb command failed (details withheld to protect credentials)",
        )
        return result.stdout.decode("utf-8", errors="replace")

    try:
        require(
            args.allow_live,
            "Pass --allow-live to authorize changes on the isolated backend",
        )
        require(
            os.environ.get("OL_LIVE_ISOLATED") == "1",
            "Set OL_LIVE_ISOLATED=1 only for a fresh disposable backend",
        )
        require(
            token.strip() and "\n" not in token and "\r" not in token,
            "Set a valid OL_API_TOKEN",
        )
        server = os.environ.get("OL_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
        parsed = urllib.parse.urlsplit(server)
        require(
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.path in {"", "/"},
            "Use a localhost HTTP backend with a dedicated port",
        )
        port = parsed.port or 80
        source = urllib.parse.urldefrag(
            os.environ.get(
                "OL_SMOKE_URL", "https://docs.python.org/3/tutorial/introduction.html"
            )
        )[0]
        source_parsed = urllib.parse.urlsplit(source)
        require(
            source_parsed.scheme == "https"
            and source_parsed.hostname
            and not source_parsed.username
            and not source_parsed.password,
            "OL_SMOKE_URL must be a public HTTPS article without credentials",
        )

        # Do not proxy credentials, or follow an authentication redirect to another host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )

        def get(path):
            request = urllib.request.Request(
                server + path, headers={"Authorization": "Bearer " + token}
            )
            with opener.open(request, timeout=30) as response:
                require(response.status == 200, "Backend GET preflight failed")
                return json.load(response)

        stage = "empty isolated backend preflight"
        require(get("/api/v1/health").get("status") == "ok", "Backend health failed")
        for path in (
            "/api/v1/feed/today",
            "/api/v1/library",
            "/api/v1/history",
            "/api/v1/reviews/due",
        ):
            require(
                get(path) == [],
                "Backend must have empty feed/library/history/due queues; use a fresh database",
            )

        stage = "device and acceptance APK preflight"
        adb_prefix = ["adb"]
        devices = [
            line.split()[0]
            for line in adb("devices").splitlines()[1:]
            if len(line.split()) == 2 and line.split()[1] == "device"
        ]
        serial = args.serial or (devices[0] if len(devices) == 1 else None)
        require(serial in devices, "Select one authorized device with --serial")
        adb_prefix += ["-s", serial]
        require(
            INSTRUMENTATION in adb("shell", "pm", "list", "instrumentation"),
            "Install acceptance app and test APKs first",
        )
        require(
            adb("shell", "run-as", PACKAGE, "pwd").strip().endswith("/" + PACKAGE),
            "Acceptance sandbox must be debuggable",
        )
        # A previous interrupted run is never overwritten or silently adopted.
        exists = subprocess.run(
            [*adb_prefix, "shell", "run-as", PACKAGE, "test", "-e", CONFIG],
            capture_output=True,
            env=child_env,
            check=False,
            timeout=30,
        )
        require(
            exists.returncode == 1,
            "Private live config already exists or cannot be checked; resolve the previous run first",
        )
        remote = "tcp:" + str(port)
        require(
            not any(
                remote in line.split()[1:2]
                for line in adb("reverse", "--list").splitlines()
            ),
            "The device port already has an adb reverse mapping; choose a dedicated backend port",
        )
        adb("reverse", "--no-rebind", remote, remote)
        owns_reverse = True

        stage = "private acceptance configuration"
        config = json.dumps(
            {
                "server_url": "http://127.0.0.1:" + str(port),
                "token": token,
                "source_url": source,
                "isolated_backend": True,
            }
        ).encode()
        # The only remote shell text is a constant path; secret bytes use stdin.
        seeded = True
        adb(
            "shell",
            "run-as",
            PACKAGE,
            "sh",
            "-c",
            "'umask 077; mkdir -p files; cat > files/live-learning-loop.json'",
            data=config,
        )

        stage = "live Android learning loop"
        output = adb(
            "shell",
            "am",
            "instrument",
            "-w",
            "-r",
            "-e",
            "class",
            CLASS,
            "-e",
            "liveLearningLoop",
            "true",
            INSTRUMENTATION,
            timeout=1200,
        )
        report = (
            Path(__file__).resolve().parents[1]
            / ".omc"
            / "android-live-instrumentation.log"
        )
        report.parent.mkdir(exist_ok=True)
        descriptor = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(output.replace(token, "<redacted>"))
        require(
            "INSTRUMENTATION_STATUS: class=" + CLASS in output,
            "The selected live test class did not run",
        )
        require(
            re.search(r"OK\s*\(1 test\)", output) is not None
            and "FAILURES!!!" not in output
            and "INSTRUMENTATION_FAILED" not in output
            and "INSTRUMENTATION_STATUS_CODE: -2" not in output
            and "INSTRUMENTATION_STATUS_CODE: -1" not in output
            and "INSTRUMENTATION_STATUS_CODE: -3" not in output
            and "INSTRUMENTATION_STATUS_CODE: -4" not in output,
            "Live instrumentation did not pass exactly one test; inspect device/backend privately",
        )
        print("PASS: live Android learning loop (one selected test)", flush=True)
        result_code = 0
    except Exception as error:  # noqa: BLE001 - exceptions can contain private request data.
        # urllib and subprocess exceptions may contain private URLs or stdout.
        detail = (
            str(error)
            if isinstance(error, RuntimeError)
            else "prerequisite or command failed; inspect locally"
        )
        print(
            "FAIL: " + stage + ": " + detail.replace(token, "<redacted>")
            if token
            else "FAIL: " + stage + ": " + detail,
            file=sys.stderr,
            flush=True,
        )
    finally:
        cleanup_failed = False
        if adb_prefix and seeded:
            try:
                adb("shell", "run-as", PACKAGE, "rm", "-f", CONFIG)
            except Exception:  # noqa: BLE001 - cleanup errors must not expose credentials.
                cleanup_failed = True
        if adb_prefix and owns_reverse:
            try:
                adb("reverse", "--remove", remote)
            except Exception:  # noqa: BLE001 - cleanup errors must not expose credentials.
                cleanup_failed = True
        if cleanup_failed:
            print(
                "FAIL: remove this run's private acceptance config/reverse mapping manually",
                file=sys.stderr,
            )
            # A passing test with failed credential cleanup is still a failed run.
            result_code = 1
    return result_code


if __name__ == "__main__":
    sys.exit(main())
