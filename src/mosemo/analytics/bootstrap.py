"""Analytics assembly for the assembly roots: mapping, handlers and PSL rules."""

from importlib.resources import files

from mosemo.shared.application.events import EventDispatcher

__all__ = ["map_tables", "public_suffix_rules", "register_handlers"]

PUBLIC_SUFFIX_LIST = "public_suffix_list.dat"


def map_tables() -> None:
    """Map the analytics tables; analytics owns no table yet."""


def register_handlers(dispatcher: EventDispatcher) -> None:
    """Subscribe the analytics handlers; analytics has no subscription yet."""


def public_suffix_rules() -> str:
    """The bundled PSL snapshot text for `UsageTargetResolver` (never fetched live)."""
    resource = files("mosemo.analytics.domain").joinpath(PUBLIC_SUFFIX_LIST)
    return resource.read_text(encoding="utf-8")
