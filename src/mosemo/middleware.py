import asyncio
from collections.abc import Awaitable, Callable

from starlette.types import Message, Receive, Scope, Send

SENSITIVE_QUERY_PATHS = frozenset({"/api/v1/auth/kakao/callback"})


class RedactSensitiveQueryStringMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in SENSITIVE_QUERY_PATHS:
            await self._app(scope, receive, send)
            return

        async def send_without_query_string(message: Message) -> None:
            if message["type"] == "http.response.start":
                scope["query_string"] = b""
            await send(message)

        await self._app(scope, receive, send_without_query_string)


class BatchBodyLimitMiddleware:
    """Bound bytes while receiving, including whitespace and the JSON envelope."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        limit: int = 512 * 1024,
        admission_timeout: float = 10.0,
    ):
        if admission_timeout <= 0:
            raise ValueError("admission timeout must be positive")
        self._app = app
        self._limit = limit
        self._admission_timeout = admission_timeout

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"]
            not in {
                "/api/v1/activities/batches",
                "/api/v1/activities/batches/legacy",
            }
        ):
            await self._app(scope, receive, send)
            return
        from mosemo.exceptions import ErrorCode
        from mosemo.responses import error_response

        if scope["path"] == "/api/v1/activities/batches/legacy":
            await self._bounded_request(scope, receive, send)
            return

        response_started = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            async with asyncio.timeout(self._admission_timeout):
                await self._bounded_request(scope, receive, tracked_send)
        except TimeoutError:
            # Never attempt a second response after headers have been sent.
            if response_started:
                raise
            await error_response(
                ErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE, headers={"Retry-After": "1"}
            )(scope, receive, send)

    async def _bounded_request(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        from mosemo.exceptions import ErrorCode
        from mosemo.responses import error_response

        body = bytearray()
        try:
            async with asyncio.timeout(self._admission_timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self._limit:
                        await error_response(ErrorCode.ACTIVITY_BATCH_TOO_LARGE)(
                            scope, receive, send
                        )
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await error_response(
                ErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE, headers={"Retry-After": "1"}
            )(scope, receive, send)
            return
        consumed = False

        async def bounded_receive() -> Message:
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self._app(scope, bounded_receive, send)
