"""Request body cap as pure ASGI middleware (declared Content-Length and streamed byte count).

The middleware answers 413 itself. It never relies on an exception travelling through the
framework (FastAPI turns body-read errors into 400), and it stops reading at the first
chunk that crosses the cap, so memory stays bounded for chunked or lying clients.
"""

import json
from collections.abc import Callable

_DISCONNECT = {"type": "http.disconnect"}


class BodyLimitMiddleware:
    def __init__(self, app, limit: Callable[[dict], int]):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cap = self.limit(scope)
        declared = [v for k, v in scope["headers"] if k == b"content-length" and v.isdigit()]
        if declared and max(int(v) for v in declared) > cap:
            await self._reject(send)
            return
        seen = 0
        started = False
        rejected = False

        async def counting_receive():
            nonlocal seen, rejected
            if rejected:
                return _DISCONNECT
            message = await receive()
            if message["type"] != "http.request":
                return message
            seen += len(message.get("body", b""))
            if seen > cap and not started:
                rejected = True  # Drop the chunk, answer 413 now, tell the app the client is gone.
                await self._reject(send)
                return _DISCONNECT
            return message

        async def guarded_send(message):
            nonlocal started
            if rejected:
                return  # The app's own follow-up (e.g. a 400 for the aborted read) is discarded.
            started = started or message["type"] == "http.response.start"
            await send(message)

        await self.app(scope, counting_receive, guarded_send)

    @staticmethod
    async def _reject(send):
        body = json.dumps({"detail": "Request body too large"}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": body})
