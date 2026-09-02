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
