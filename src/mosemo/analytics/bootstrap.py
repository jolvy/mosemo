"""Analytics assembly for the assembly roots: mapping and handlers."""

from mosemo.shared.application.events import EventDispatcher

__all__ = ["map_tables", "register_handlers"]


def map_tables() -> None:
    """Map the analytics tables; analytics owns no table yet."""


def register_handlers(dispatcher: EventDispatcher) -> None:
    """Subscribe the analytics handlers; analytics has no subscription yet."""
