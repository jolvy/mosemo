import asyncio

from mosemo.middleware import RedactSensitiveQueryStringMiddleware


def test_sensitive_callback_query_is_cleared_before_response_start() -> None:
    observed_query_strings: list[bytes] = []
    scope = {
        "type": "http",
        "path": "/api/v1/auth/kakao/callback",
        "query_string": b"code=secret&state=secret",
    }

    async def app(scope, receive, send) -> None:
        query_string = scope["query_string"]
        assert isinstance(query_string, bytes)
        observed_query_strings.append(query_string)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message) -> None:
        if message["type"] == "http.response.start":
            query_string = scope["query_string"]
            assert isinstance(query_string, bytes)
            observed_query_strings.append(query_string)

    asyncio.run(
        RedactSensitiveQueryStringMiddleware(app)(scope, receive, send)  # type: ignore[arg-type]
    )

    assert observed_query_strings == [
        b"code=secret&state=secret",
        b"",
    ]
