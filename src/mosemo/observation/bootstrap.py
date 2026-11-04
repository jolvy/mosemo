"""Observation assembly for the assembly roots: mapping, object graph, routers."""

from fastapi import APIRouter

from mosemo.identity.public import AccountDirectory, DeviceDirectory
from mosemo.labels.public import LabelCatalog
from mosemo.observation.application.activities import (
    GetObservationTimeline,
    RecordActivity,
)
from mosemo.observation.application.batches import (
    AcceptActivityBatch,
    ProcessActivityBatch,
    ProcessLegacyActivityBatch,
)
from mosemo.observation.application.focus_sessions import (
    CompleteFocusSession,
    ListCompletedFocusSessions,
    StartFocusSession,
)
from mosemo.observation.application.ports import (
    ActivityBatchPublisher,
    ActivityDeliveryUnavailableError,
)
from mosemo.observation.application.timeline import ObservationTimeline
from mosemo.observation.infrastructure.activity_records import (
    ActivityRecordRepository,
)
from mosemo.observation.infrastructure.focus_sessions import FocusSessionRepository
from mosemo.observation.infrastructure.tables import map_tables
from mosemo.observation.infrastructure.timeline_segments import (
    TimelineSegmentRepository,
)
from mosemo.observation.presentation.activities.router import (
    BatchPublisherProvider,
)
from mosemo.observation.presentation.activities.router import (
    create_router as create_activities_router,
)
from mosemo.observation.presentation.activities.schemas import ActivityBatchRequest
from mosemo.observation.presentation.error_docs import ERROR_DOCS
from mosemo.observation.presentation.focus_sessions.router import (
    create_router as create_focus_sessions_router,
)
from mosemo.observation.presentation.messages import ActivityRegistrationRequested
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher

__all__ = [
    "ERROR_DOCS",
    "ActivityBatchPublisher",
    "ActivityBatchRequest",
    "ActivityDeliveryUnavailableError",
    "ActivityRegistrationRequested",
    "Observation",
    "ProcessActivityBatch",
    "create_observation_timeline",
    "create_process_activity_batch",
    "map_tables",
]


def create_observation_timeline() -> ObservationTimeline:
    return ObservationTimeline(segments=TimelineSegmentRepository())


def create_process_activity_batch(
    *, devices: DeviceDirectory, dispatcher: EventDispatcher, clock: Clock
) -> ProcessActivityBatch:
    """The Consumer path use case; the Consumer opens a session per message."""
    return ProcessActivityBatch(devices=devices, dispatcher=dispatcher, clock=clock)


class Observation:
    """Stateless observation objects built once when an assembly root starts."""

    def __init__(
        self,
        *,
        accounts: AccountDirectory,
        devices: DeviceDirectory,
        labels: LabelCatalog,
        dispatcher: EventDispatcher,
        clock: Clock,
    ) -> None:
        focus_sessions = FocusSessionRepository()
        self.observation_timeline = create_observation_timeline()
        self.record_activity = RecordActivity(
            devices=devices, dispatcher=dispatcher, clock=clock
        )
        self.get_observation_timeline = GetObservationTimeline(
            timeline=self.observation_timeline, accounts=accounts, clock=clock
        )
        self.accept_activity_batch = AcceptActivityBatch(devices=devices)
        self.process_activity_batch = create_process_activity_batch(
            devices=devices, dispatcher=dispatcher, clock=clock
        )
        self.process_legacy_activity_batch = ProcessLegacyActivityBatch(
            devices=devices, dispatcher=dispatcher, clock=clock
        )
        self.start_focus_session = StartFocusSession(
            focus_sessions=focus_sessions, devices=devices, dispatcher=dispatcher
        )
        self.complete_focus_session = CompleteFocusSession(
            focus_sessions=focus_sessions,
            activity_records=ActivityRecordRepository(),
            labels=labels,
            dispatcher=dispatcher,
        )
        self.list_completed_focus_sessions = ListCompletedFocusSessions(
            focus_sessions=focus_sessions, accounts=accounts, clock=clock
        )

    def activities_router(
        self, *, get_batch_publisher: BatchPublisherProvider
    ) -> APIRouter:
        return create_activities_router(
            accept_activity_batch=self.accept_activity_batch,
            process_legacy_activity_batch=self.process_legacy_activity_batch,
            get_observation_timeline=self.get_observation_timeline,
            get_batch_publisher=get_batch_publisher,
        )

    def focus_sessions_router(self) -> APIRouter:
        return create_focus_sessions_router(
            start_focus_session=self.start_focus_session,
            complete_focus_session=self.complete_focus_session,
            list_completed_focus_sessions=self.list_completed_focus_sessions,
        )
