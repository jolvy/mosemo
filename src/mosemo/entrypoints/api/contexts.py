"""Bounded context object graphs that the API process builds once at startup."""

from mosemo.entrypoints.api.config import get_api_config
from mosemo.identity import bootstrap as identity_bootstrap
from mosemo.labeling import bootstrap as labeling_bootstrap
from mosemo.labels import bootstrap as labels_bootstrap
from mosemo.observation import bootstrap as observation_bootstrap
from mosemo.platform.clock import utc_now
from mosemo.platform.providers import get_event_dispatcher

identity_bootstrap.map_tables()
labels_bootstrap.map_tables()
observation_bootstrap.map_tables()
labeling_bootstrap.map_tables()
labels_bootstrap.register_handlers(get_event_dispatcher())

IDENTITY = identity_bootstrap.Identity(
    dispatcher=get_event_dispatcher(),
    clock=utc_now,
    config=get_api_config().identity(),
)
LABELS = labels_bootstrap.Labels(accounts=IDENTITY.account_directory)
OBSERVATION = observation_bootstrap.Observation(
    accounts=IDENTITY.account_directory,
    devices=IDENTITY.device_directory,
    labels=LABELS.label_catalog,
    dispatcher=get_event_dispatcher(),
    clock=utc_now,
)
LABELING = labeling_bootstrap.Labeling(
    accounts=IDENTITY.account_directory,
    labels=LABELS.label_catalog,
    timeline=OBSERVATION.observation_timeline,
    dispatcher=get_event_dispatcher(),
    clock=utc_now,
)
