"""Manual acceptance against a real API, worker, PostgreSQL, and model.

Run with backend/.venv/bin/python scripts/smoke_live.py from the repository root.
Set OL_API_TOKEN in the environment; use a fresh source/database for a due review.
This changes live data: ingest, save, tutor twice, and rate one review.
"""

import os
import sys
import time
from datetime import UTC, datetime
from urllib.parse import urldefrag

import httpx


def require(condition):
    if not condition:
        raise RuntimeError("Acceptance check failed")


def main():
    stage = "configuration"
    try:
        token = os.environ["OL_API_TOKEN"]
        require(bool(token.strip()))
        server = os.environ.get("OL_SERVER_URL", "http://127.0.0.1:8000").rstrip("/")
        source = os.environ.get(
            "OL_SMOKE_URL", "https://docs.python.org/3/tutorial/introduction.html"
        )
        source = urldefrag(source)[0]
        with httpx.Client(base_url=server, timeout=180, trust_env=False) as client:

            def call(method, path, expected=200, **kwargs):
                response = client.request(method, path, **kwargs)
                require(response.status_code == expected)
                return response.json()

            stage = "health and authentication"
            require(call("GET", "/health")["status"] == "ok")
            require(client.get("/api/v1/feed/today").status_code == 401)
            client.headers["Authorization"] = "Bearer " + token
            require(call("GET", "/api/v1/health")["status"] == "ok")
            print("PASS: health and authentication", flush=True)

            stage = "ingestion"
            job = call("POST", "/api/v1/ingest/url", 202, json={"url": source})
            deadline = time.monotonic() + 600
            while job["status"] != "completed":
                require(job["status"] in {"queued", "running"})
                remaining = deadline - time.monotonic()
                require(remaining > 0)
                time.sleep(min(2, remaining))
                remaining = deadline - time.monotonic()
                require(remaining > 0)
                job = call("GET", "/api/v1/jobs/" + job["id"], timeout=min(180, remaining))
            print("PASS: ingestion completed", flush=True)

            stage = "feed and source attribution"
            candidates = [
                item
                for item in call("GET", "/api/v1/feed/today")
                if item["source_url"].rstrip("/") == source.rstrip("/")
            ]
            require(bool(candidates))
            insight = min(candidates, key=lambda item: item["saved"])
            require(
                all(insight[key].strip() for key in ("document_id", "source_title", "evidence"))
            )
            detail = call("GET", "/api/v1/insights/" + insight["id"])
            require(detail["document_id"] == insight["document_id"])
            print("PASS: feed and source attribution", flush=True)

            stage = "save and library"
            saved = call("POST", "/api/v1/insights/" + insight["id"] + "/save")
            require(saved["saved"])
            require(any(item["id"] == insight["id"] for item in call("GET", "/api/v1/library")))
            print("PASS: save and library", flush=True)

            for stage, message in (
                ("tutor start", ""),
                ("tutor explanation", insight["idea"]),
            ):
                tutor = call(
                    "POST",
                    "/api/v1/tutor/chat",
                    json={"insight_id": insight["id"], "message": message},
                )
                require(tutor["answer"].strip() and tutor["question"].strip())
                require(bool(tutor["citations"]))
                require(
                    all(
                        citation["document_id"] == insight["document_id"]
                        and citation["source_url"] == insight["source_url"]
                        and citation["passage"].strip()
                        for citation in tutor["citations"]
                    )
                )
                print("PASS: " + stage, flush=True)

            stage = "review scheduling"
            review_id = tutor["review_id"]
            require(any(item["id"] == review_id for item in call("GET", "/api/v1/reviews/due")))
            rated = call(
                "POST",
                "/api/v1/reviews/" + review_id + "/answer",
                json={"rating": "remembered"},
            )
            require(rated["interval_days"] == 3)
            require(datetime.fromisoformat(rated["due_at"]) > datetime.now(UTC))
            require(all(item["id"] != review_id for item in call("GET", "/api/v1/reviews/due")))
            print("PASS: review scheduled for a future date", flush=True)
    except Exception:  # noqa: BLE001 - exception details can contain private request data.
        print("FAIL: " + stage, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
