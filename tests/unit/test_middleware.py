import asyncio

import pytest

from mosemo.middleware import (
    BatchBodyLimitMiddleware,
    RedactSensitiveQueryStringMiddleware,
)

BATCH_SCOPE = {
    "type": "http",
    "method": "POST",
    "path": "/api/v1/activities/batches",
    "headers": [],
}


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


def test_batch_body_limit_requires_positive_admission_timeout() -> None:
    async def app(scope, receive, send) -> None:
        raise AssertionError("must not be called")

    with pytest.raises(ValueError, match="admission timeout must be positive"):
        BatchBodyLimitMiddleware(app, admission_timeout=0)


@pytest.mark.asyncio
async def test_batch_body_limit_stops_when_client_disconnects_before_body() -> None:
    sent = []

    async def app(scope, receive, send) -> None:
        raise AssertionError("must not be called")

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message) -> None:
        sent.append(message)

    await BatchBodyLimitMiddleware(app)(dict(BATCH_SCOPE), receive, send)

    assert sent == []


@pytest.mark.asyncio
async def test_batch_body_limit_replays_body_once_then_reads_downstream() -> None:
    received = []

    async def app(scope, receive, send) -> None:
        received.append(await receive())
        received.append(await receive())

    messages = iter(
        [
            {"type": "http.request", "body": b"{", "more_body": True},
            {"type": "http.request", "body": b"}", "more_body": False},
            {"type": "http.disconnect"},
        ]
    )

    async def receive():
        return next(messages)

    async def send(message) -> None:
        raise AssertionError("must not respond")

    await BatchBodyLimitMiddleware(app)(dict(BATCH_SCOPE), receive, send)

    assert received == [
        {"type": "http.request", "body": b"{}", "more_body": False},
        {"type": "http.disconnect"},
    ]


@pytest.mark.asyncio
async def test_batch_admission_timeout_after_response_start_is_not_answered_twice() -> (
    None
):
    sent = []

    async def app(scope, receive, send) -> None:
        await receive()
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await asyncio.Event().wait()

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message) -> None:
        sent.append(message)

    middleware = BatchBodyLimitMiddleware(app, admission_timeout=0.02)
    task = asyncio.create_task(middleware(dict(BATCH_SCOPE), receive, send))
    await asyncio.wait({task}, timeout=1)

    assert task.done(), "admission deadline did not cancel the application"
    assert isinstance(task.exception(), TimeoutError)

    assert sent == [{"type": "http.response.start", "status": 202, "headers": []}]
