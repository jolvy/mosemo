"""Request-scoped and shared providers that bounded contexts import (ADR 0011)."""

from functools import cache
from typing import cast

import httpx2
from fastapi import Request

from mosemo.platform.clock import utc_now
from mosemo.platform.config import Config, get_config
from mosemo.platform.database.engine import get_session
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher

__all__ = [
    "Config",
    "get_clock",
    "get_config",
    "get_event_dispatcher",
    "get_http_client",
    "get_session",
]


def get_clock() -> Clock:
    return utc_now


@cache
def get_event_dispatcher() -> EventDispatcher:
    return EventDispatcher()


def get_http_client(request: Request) -> httpx2.AsyncClient:
    """The HTTP client that the API lifespan opened for this application."""
    return cast(httpx2.AsyncClient, request.state.http_client)
