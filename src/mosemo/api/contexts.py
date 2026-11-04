"""Bounded context object graphs that the API process builds once at startup."""

from mosemo.api.default_labels import register_default_labels
from mosemo.identity import bootstrap as identity_bootstrap
from mosemo.infra.clock import utc_now
from mosemo.infra.config import get_config
from mosemo.infra.providers import get_event_dispatcher

identity_bootstrap.map_tables()
register_default_labels(get_event_dispatcher())

IDENTITY = identity_bootstrap.Identity(
    dispatcher=get_event_dispatcher(), clock=utc_now, config=get_config()
)
