import asyncio
import json

import pytest

from mosemo.middleware import BatchBodyLimitMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("stall", ["body", "application"])
async def test_batch_admission_deadline_cancels_stalled_work_and_returns_retryable_error(
    stall: str,
) -> None:
    entered = asyncio.Event()
    cleaned = asyncio.Event()
    never = asyncio.Event()
    messages = []

    async def receive():
        if stall == "body":
            entered.set()
            try:
                await never.wait()
            finally:
                cleaned.set()
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def application(scope, receive, send):
        assert stall == "application"
        entered.set()
        try:
            await never.wait()
        finally:
            cleaned.set()

    async def send(message):
        messages.append(message)

    middleware = BatchBodyLimitMiddleware(application, admission_timeout=0.02)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/activities/batches",
        "raw_path": b"/api/v1/activities/batches",
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("testclient", 1234),
    }
    await asyncio.wait_for(middleware(scope, receive, send), timeout=1)
    assert entered.is_set() and cleaned.is_set()
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 503
    body = json.loads(messages[-1]["body"])
    assert body["error"]["status"] == "ACTIVITY_DELIVERY_UNAVAILABLE"
    assert sum(m["type"] == "http.response.start" for m in messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stall", ["body", "application"])
async def test_legacy_limits_body_receive_but_allows_longer_processing(stall):
    messages = []

    async def receive():
        if stall == "body":
            await asyncio.Event().wait()
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def application(scope, receive, send):
        await receive()
        await asyncio.sleep(0.04)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def send(message):
        messages.append(message)

    middleware = BatchBodyLimitMiddleware(application, admission_timeout=0.02)
    await asyncio.wait_for(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/activities/batches/legacy",
            },
            receive,
            send,
        ),
        timeout=1,
    )
    assert messages[0]["status"] == (503 if stall == "body" else 200)
    if stall == "body":
        assert (b"retry-after", b"1") in messages[0]["headers"]
