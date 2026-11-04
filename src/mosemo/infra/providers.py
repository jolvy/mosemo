"""Request-scoped and shared providers that bounded contexts import (ADR 0011)."""

from functools import cache

from mosemo.infra.clock import utc_now
from mosemo.infra.config import get_config
from mosemo.infra.database import get_session
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher

__all__ = ["get_clock", "get_config", "get_event_dispatcher", "get_session"]


def get_clock() -> Clock:
    return utc_now


@cache
def get_event_dispatcher() -> EventDispatcher:
    return EventDispatcher()
