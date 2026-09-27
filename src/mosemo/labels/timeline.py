"""Label timeline grouping rules independent of persistence and API schemas."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TimelineConfirmation:
    confirmation_id: UUID
    label_id: UUID | None
    display_name: str | None
    updated_at: datetime

    def __post_init__(self) -> None:
        if (self.label_id is None) != (self.display_name is None):
            raise ValueError("confirmed label and display name must appear together")

    def same_selection_as(self, other: TimelineConfirmation) -> bool:
        return self.label_id == other.label_id

    def version_part(self) -> str:
        return (
            f"{self.confirmation_id}:"
            f"{self.updated_at.astimezone(UTC).isoformat()}:"
            f"{self.label_id}"
        )


@dataclass(frozen=True, slots=True)
class ClosedActivity:
    segment_id: UUID
    segment_version: str
    started_at: datetime
    ended_at: datetime
    last_observed_at: datetime
    context: dict[str, object]
    confirmation: TimelineConfirmation | None

    def has_same_selection_as(self, other: ClosedActivity) -> bool:
        if self.confirmation is None or other.confirmation is None:
            return self.confirmation is None and other.confirmation is None
        return self.confirmation.same_selection_as(other.confirmation)

    def version_part(self) -> str:
        state = (
            self.confirmation.version_part()
            if self.confirmation is not None
            else "pending"
        )
        return f"{self.segment_id}:{self.segment_version}:{state}"


@dataclass(frozen=True, slots=True)
class InProgressActivity:
    segment_id: UUID
    started_at: datetime
    last_observed_at: datetime
    context: dict[str, object]


@dataclass(frozen=True, slots=True)
class OpaqueActivity:
    segment_id: UUID
    started_at: datetime
    ended_at: datetime | None
    last_observed_at: datetime
    context: dict[str, object]


@dataclass(frozen=True, slots=True)
class CaptureGap:
    segment_id: UUID
    started_at: datetime
    ended_at: datetime | None
    reason: str


@dataclass(frozen=True, slots=True)
class SegmentSnapshot:
    segment_id: UUID
    segment_version: str


@dataclass(frozen=True, slots=True)
class GroupSnapshot:
    group_version: str
    segments: tuple[SegmentSnapshot, ...]


class ActivityGroup:
    def __init__(self, first: ClosedActivity) -> None:
        self._members = [first]

    @property
    def members(self) -> tuple[ClosedActivity, ...]:
        return tuple(self._members)

    @property
    def started_at(self) -> datetime:
        return self._members[0].started_at

    @property
    def ended_at(self) -> datetime:
        return self._members[-1].ended_at

    @property
    def confirmation(self) -> TimelineConfirmation | None:
        return self._members[0].confirmation

    @property
    def version(self) -> str:
        parts = [member.version_part() for member in self._members]
        return sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()

    def _accepts(self, activity: ClosedActivity) -> bool:
        first = self._members[0]
        return self.ended_at == activity.started_at and first.has_same_selection_as(
            activity
        )

    def try_append(self, activity: ClosedActivity) -> bool:
        if not self._accepts(activity):
            return False
        self._members.append(activity)
        return True

    def matches(self, snapshot: GroupSnapshot) -> bool:
        members = tuple(
            SegmentSnapshot(member.segment_id, member.segment_version)
            for member in self._members
        )
        return members == snapshot.segments and self.version == snapshot.group_version


TimelineItem = ActivityGroup | InProgressActivity | OpaqueActivity | CaptureGap
TimelineEntry = ClosedActivity | InProgressActivity | OpaqueActivity | CaptureGap


class LabelTimeline:
    def __init__(self) -> None:
        self._items: list[TimelineItem] = []

    @property
    def items(self) -> tuple[TimelineItem, ...]:
        return tuple(self._items)

    def add(self, entry: TimelineEntry) -> None:
        if not isinstance(entry, ClosedActivity):
            self._items.append(entry)
            return
        last = self._items[-1] if self._items else None
        if isinstance(last, ActivityGroup) and last.try_append(entry):
            return
        self._items.append(ActivityGroup(entry))

    def contains(self, snapshot: GroupSnapshot) -> bool:
        return any(
            item.matches(snapshot)
            for item in self._items
            if isinstance(item, ActivityGroup)
        )
