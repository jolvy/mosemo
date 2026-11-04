"""Bounded context object graphs that the API process builds once at startup."""

from mosemo.identity import bootstrap as identity_bootstrap
from mosemo.infra.clock import utc_now
from mosemo.infra.config import get_config
from mosemo.infra.providers import get_event_dispatcher
from mosemo.labels import bootstrap as labels_bootstrap

identity_bootstrap.map_tables()
labels_bootstrap.map_tables()
labels_bootstrap.register_handlers(get_event_dispatcher())

IDENTITY = identity_bootstrap.Identity(
    dispatcher=get_event_dispatcher(), clock=utc_now, config=get_config()
)
LABELS = labels_bootstrap.Labels(accounts=IDENTITY.account_directory)
