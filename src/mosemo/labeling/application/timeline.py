import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.public import AccountDirectory
from mosemo.infra.transactions import read_snapshot
from mosemo.labeling.domain.confirmations import ActivityLabelConfirmation
from mosemo.labeling.infrastructure.confirmations import ConfirmationRepository
from mosemo.labels.public import LabelCatalog
from mosemo.observation.public import (
    ActivityTimelineKind,
    ObservationTimeline,
    ObservedSegment,
)
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.use_case import UseCase
from mosemo.shared.domain.timezones import timeline_date_window


def group_member_version(
    *,
    segment_id: UUID,
    version: str,
    confirmation: ActivityLabelConfirmation | None,
) -> str:
    if confirmation is None:
        return f"{segment_id}:{version}:pending"
    return (
        f"{segment_id}:{version}:{confirmation.id}:"
        f"{confirmation.updated_at.astimezone(UTC).isoformat()}:"
        f"{confirmation.label_id}"
    )


def group_version(members: list[str]) -> str:
    return sha256(json.dumps(members, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LabelTimelineEntry:
    """A labelable segment with the confirmation that applies to its version.

    `display_name` is the confirmed label's current name; it is `None` for
    pending and unclassified entries.
    """

    segment: ObservedSegment
    version: str
    ended_at: datetime
    confirmation: ActivityLabelConfirmation | None
    display_name: str | None

    def joins(self, previous: LabelTimelineEntry) -> bool:
        if previous.ended_at != self.segment.started_at:
            return False
        if previous.confirmation is None or self.confirmation is None:
            return previous.confirmation is None and self.confirmation is None
        return previous.confirmation.label_id == self.confirmation.label_id


@dataclass(frozen=True, slots=True)
class LabelTimelineGroup:
    """Adjacent closed detailed segments with the same label state, in order."""

    entries: list[LabelTimelineEntry]

    @property
    def version(self) -> str:
        return group_version(
            [
                group_member_version(
                    segment_id=entry.segment.id,
                    version=entry.version,
                    confirmation=entry.confirmation,
                )
                for entry in self.entries
            ]
        )


# A group of labelable segments, or another segment (in progress, opaque,
# capture gap) that stands alone.
type LabelTimelineItem = LabelTimelineGroup | ObservedSegment


def _timeline_entry(
    segment: ObservedSegment,
    *,
    confirmations: dict[UUID, ActivityLabelConfirmation],
    names: dict[UUID, str],
) -> LabelTimelineEntry:
    ended_at = segment.ended_at
    version = segment.version
    assert ended_at is not None
    assert version is not None
    confirmation = confirmations.get(segment.first_event_id)
    if confirmation is None or not confirmation.applies_to(version):
        confirmation = None
    return LabelTimelineEntry(
        segment=segment,
        version=version,
        ended_at=ended_at,
        confirmation=confirmation,
        display_name=(
            None
            if confirmation is None or confirmation.label_id is None
            else names.get(confirmation.label_id)
        ),
    )


def _group_items(
    segments: list[ObservedSegment],
    *,
    confirmations: dict[UUID, ActivityLabelConfirmation],
    names: dict[UUID, str],
) -> list[LabelTimelineItem]:
    items: list[LabelTimelineItem] = []
    group: list[LabelTimelineEntry] = []
    for segment in segments:
        if segment.kind is not ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY:
            if group:
                items.append(LabelTimelineGroup(group))
                group = []
            items.append(segment)
            continue
        entry = _timeline_entry(segment, confirmations=confirmations, names=names)
        if group and not entry.joins(group[-1]):
            items.append(LabelTimelineGroup(group))
            group = []
        group.append(entry)
    if group:
        items.append(LabelTimelineGroup(group))
    return items


class GetLabelTimeline(UseCase):
    def __init__(
        self,
        *,
        confirmations: ConfirmationRepository,
        labels: LabelCatalog,
        timeline: ObservationTimeline,
        accounts: AccountDirectory,
        clock: Clock,
    ) -> None:
        self._confirmations = confirmations
        self._labels = labels
        self._timeline = timeline
        self._accounts = accounts
        self._clock = clock

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, day: date | None
    ) -> list[LabelTimelineItem]:
        """The account's label timeline for `day`, or today, in its timezone.

        Raises identity's `AccountNotFoundError` when the account does not exist.
        """
        now = self._clock()
        async with read_snapshot(db_session):
            account_timezone = await self._accounts.get_timezone(db_session, account_id)
            window = timeline_date_window(
                account_timezone=account_timezone, requested_date=day, now=now
            )
            if window is None:
                return []
            _, start, end = window
            segments = await self._timeline.list_window(
                db_session, account_id=account_id, start=start, end=end, now=now
            )
            confirmations = await self._confirmations.list_by_first_events(
                db_session,
                account_id=account_id,
                first_event_ids={
                    segment.first_event_id
                    for segment in segments
                    if segment.kind is ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY
                },
            )
            # Archived labels keep their names; a missing label leaves it `None`.
            names = await self._labels.find_names(
                db_session,
                account_id=account_id,
                label_ids={
                    confirmation.label_id
                    for confirmation in confirmations
                    if confirmation.label_id is not None
                },
            )
        return _group_items(
            segments,
            confirmations={
                confirmation.first_event_id: confirmation
                for confirmation in confirmations
            },
            names=names,
        )
