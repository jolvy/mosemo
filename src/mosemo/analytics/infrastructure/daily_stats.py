"""Stored daily stats rows.

The JSON shapes below are part of the metric definition: changing one needs a
new definition version. Site keys are stored as their ASCII keys only.
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.analytics.domain.daily_stats import (
    SHORT_DWELL_US,
    AppUsage,
    AuxiliaryValues,
    DailyStats,
    SessionTotals,
    TargetUsage,
    UsageTime,
)
from mosemo.analytics.domain.session_analysis import Distraction, TimeComposition
from mosemo.analytics.domain.stored_stats import StoredDailyStats
from mosemo.analytics.domain.transitions import LabelTransition, TargetTransition
from mosemo.analytics.domain.usage_targets import (
    UNKNOWN_APP,
    LapseTarget,
    UsageTarget,
    UsageTargetKind,
)
from mosemo.analytics.infrastructure.tables import analytics_daily_stats

stats_table = analytics_daily_stats
UNKNOWN_APP_KIND = "unknown_app"

type Json = dict[str, Any]


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _target(target: UsageTarget) -> Json:
    return {"kind": target.kind.value, "key": target.key}


def _load_target(value: Json) -> UsageTarget:
    return UsageTarget(kind=UsageTargetKind(value["kind"]), key=value["key"])


def _lapse_target(target: LapseTarget) -> Json:
    if isinstance(target, UsageTarget):
        return _target(target)
    return {"kind": UNKNOWN_APP_KIND, "key": None}


def _load_lapse_target(value: Json) -> LapseTarget:
    if value["kind"] == UNKNOWN_APP_KIND:
        return UNKNOWN_APP
    return _load_target(value)


def stats_row(stored: StoredDailyStats) -> Json:
    stats = stored.stats
    usage = stats.usage
    sessions = stats.sessions
    composition = sessions.composition
    auxiliary = stats.auxiliary
    return {
        "account_id": stored.account_id,
        "local_date": stored.local_date,
        "source_version": stored.source_version,
        "definition_version": stored.definition_version,
        "timezone": stored.timezone,
        "computed_at": stored.computed_at,
        "observed_us": usage.observed_us,
        "unknown_app_us": usage.unknown_app_us,
        "opaque_us": usage.opaque_us,
        # An app shows its bundleId as its name (`UsageTarget.display_name`).
        "app_usage": [
            {"bundleId": row.app, "name": row.app, "us": row.us} for row in usage.apps
        ],
        "target_usage": [
            {**_target(row.target), "us": row.us} for row in usage.targets
        ],
        "target_transition_count": len(stats.target_transitions),
        "target_transitions": [
            {
                "at": _instant(row.at),
                "prev": _target(row.previous),
                "next": _target(row.next),
                "dwellUs": row.previous_dwell_us,
            }
            for row in stats.target_transitions
        ],
        "label_transition_count": len(stats.label_transitions),
        "label_transitions": [
            {"at": _instant(row.at), "prev": row.previous, "next": row.next}
            for row in stats.label_transitions
        ],
        "session_count": sessions.count,
        "session_analysis_us": sessions.analysis_us,
        "session_focus_us": composition.focus_us,
        "session_lapse_us": composition.lapse_us,
        "session_unclassified_us": composition.unclassified_us,
        "session_pending_us": composition.pending_us,
        "session_opaque_us": composition.opaque_us,
        "session_unobserved_us": composition.unobserved_us,
        "session_lapse_count": composition.lapse_count,
        "session_distractions": [
            {
                **_lapse_target(row.target),
                "entries": row.entries,
                "lapseUs": row.lapse_us,
            }
            for row in sessions.distractions
        ],
        "browser_without_url_us": auxiliary.browser_without_url_us,
        "capture_gap_count": auxiliary.capture_gap_count,
        "silence_count": auxiliary.silence_count,
        "pending_us": auxiliary.pending_us,
    }


def stored_stats(row: Mapping[str, Any]) -> StoredDailyStats:
    target_transitions = tuple(
        TargetTransition(
            at=datetime.fromisoformat(item["at"]),
            previous=_load_target(item["prev"]),
            next=_load_target(item["next"]),
            previous_dwell_us=item["dwellUs"],
        )
        for item in row["target_transitions"]
    )
    stats = DailyStats(
        usage=UsageTime(
            apps=tuple(
                AppUsage(app=item["bundleId"], us=item["us"])
                for item in row["app_usage"]
            ),
            targets=tuple(
                TargetUsage(target=_load_target(item), us=item["us"])
                for item in row["target_usage"]
            ),
            unknown_app_us=row["unknown_app_us"],
            opaque_us=row["opaque_us"],
            observed_us=row["observed_us"],
        ),
        target_transitions=target_transitions,
        label_transitions=tuple(
            LabelTransition(
                at=datetime.fromisoformat(item["at"]),
                previous=item["prev"],
                next=item["next"],
            )
            for item in row["label_transitions"]
        ),
        sessions=SessionTotals(
            count=row["session_count"],
            analysis_us=row["session_analysis_us"],
            composition=TimeComposition(
                focus_us=row["session_focus_us"],
                lapse_us=row["session_lapse_us"],
                unclassified_us=row["session_unclassified_us"],
                pending_us=row["session_pending_us"],
                opaque_us=row["session_opaque_us"],
                unobserved_us=row["session_unobserved_us"],
                lapse_count=row["session_lapse_count"],
            ),
            distractions=tuple(
                Distraction(
                    target=_load_lapse_target(item),
                    entries=item["entries"],
                    lapse_us=item["lapseUs"],
                )
                for item in row["session_distractions"]
            ),
        ),
        auxiliary=AuxiliaryValues(
            # Derived from the transition list rather than stored (ADR 0016).
            short_dwell_transition_count=sum(
                1
                for item in target_transitions
                if item.previous_dwell_us < SHORT_DWELL_US
            ),
            browser_without_url_us=row["browser_without_url_us"],
            capture_gap_count=row["capture_gap_count"],
            silence_count=row["silence_count"],
            pending_us=row["pending_us"],
        ),
    )
    return StoredDailyStats(
        account_id=row["account_id"],
        local_date=row["local_date"],
        source_version=row["source_version"],
        definition_version=row["definition_version"],
        timezone=row["timezone"],
        computed_at=row["computed_at"],
        stats=stats,
    )


class DailyStatsRepository:
    async def find(
        self, db_session: AsyncSession, *, account_id: UUID, local_date: date
    ) -> StoredDailyStats | None:
        row = (
            (
                await db_session.execute(
                    select(stats_table).where(
                        stats_table.c.account_id == account_id,
                        stats_table.c.local_date == local_date,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else stored_stats(dict(row))

    async def save_if_newer(
        self, db_session: AsyncSession, stored: StoredDailyStats
    ) -> bool:
        """Insert, or replace a row with a smaller `source_version`.

        Returns whether the row was written; an older result is ignored.
        """
        statement = insert(stats_table).values(stats_row(stored))
        replaced = {
            column.name: statement.excluded[column.name]
            for column in stats_table.columns
            if not column.primary_key
        }
        written = await db_session.scalar(
            statement.on_conflict_do_update(
                index_elements=[stats_table.c.account_id, stats_table.c.local_date],
                set_=replaced,
                where=stats_table.c.source_version < statement.excluded.source_version,
            ).returning(stats_table.c.source_version)
        )
        return written is not None
