from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Sequence,
    SmallInteger,
    String,
    Table,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from mosemo.analytics.domain.markers import MarkerKind, RecomputeMarker
from mosemo.analytics.domain.stored_stats import StoredDailyStats
from mosemo.platform.database.persistence import (
    ORM_METADATA,
    TimezoneStorage,
    mapper_registry,
)

marker_version_seq = Sequence(
    "analytics_daily_marker_version_seq", data_type=BigInteger, metadata=ORM_METADATA
)

analytics_definitions = Table(
    "analytics_definitions",
    ORM_METADATA,
    Column("id", SmallInteger, primary_key=True, autoincrement=False),
    Column("version", Integer, nullable=False),
    Column("changed_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("id = 1", name="id"),
    CheckConstraint("version > 0", name="version"),
)


def _account_id() -> Column[object]:
    return Column(
        "account_id",
        Uuid,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )


analytics_daily_markers = Table(
    "analytics_daily_markers",
    ORM_METADATA,
    _account_id(),
    Column("local_date", Date, nullable=False),
    Column("version", BigInteger, nullable=False),
    Column(
        "kind",
        Enum(
            MarkerKind,
            values_callable=lambda members: [member.value for member in members],
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            length=16,
        ),
        nullable=False,
    ),
    Column("marked_at", DateTime(timezone=True), nullable=False),
    Column("eligible_at", DateTime(timezone=True), nullable=False),
    Column("attempts", SmallInteger, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("last_error", String, nullable=True),
    Column("last_failed_at", DateTime(timezone=True), nullable=True),
    PrimaryKeyConstraint("account_id", "local_date"),
    CheckConstraint("kind IN ('source', 'definition')", name="kind"),
    CheckConstraint("attempts >= 0", name="attempts"),
    CheckConstraint(
        "(last_error IS NULL) = (last_failed_at IS NULL)", name="last_failure"
    ),
    Index(
        "analytics_daily_markers_claim_idx",
        text("(kind = 'definition')"),
        "version",
        # 6 = `RetryPolicy.quarantine_threshold`; a test keeps the two equal.
        postgresql_where=text("attempts < 6"),
    ),
)

US_COLUMNS = (
    "observed_us",
    "unknown_app_us",
    "opaque_us",
    "session_analysis_us",
    "session_focus_us",
    "session_lapse_us",
    "session_unclassified_us",
    "session_pending_us",
    "session_opaque_us",
    "session_unobserved_us",
    "browser_without_url_us",
    "pending_us",
)
COUNT_COLUMNS = (
    "target_transition_count",
    "label_transition_count",
    "session_count",
    "session_lapse_count",
    "capture_gap_count",
    "silence_count",
)
JSON_COLUMNS = (
    "app_usage",
    "target_usage",
    "target_transitions",
    "label_transitions",
    "session_distractions",
)
SESSION_SIX_TIMES = (
    "session_focus_us + session_lapse_us + session_unclassified_us"
    " + session_pending_us + session_opaque_us + session_unobserved_us"
)

analytics_daily_stats = Table(
    "analytics_daily_stats",
    ORM_METADATA,
    _account_id(),
    Column("local_date", Date, nullable=False),
    Column("source_version", BigInteger, nullable=False),
    Column("definition_version", Integer, nullable=False),
    Column("timezone", TimezoneStorage(String(255)), nullable=False),
    Column("computed_at", DateTime(timezone=True), nullable=False),
    *(Column(name, BigInteger, nullable=False) for name in US_COLUMNS),
    *(Column(name, Integer, nullable=False) for name in COUNT_COLUMNS),
    *(Column(name, JSONB, nullable=False) for name in JSON_COLUMNS),
    PrimaryKeyConstraint("account_id", "local_date"),
    CheckConstraint("source_version > 0 AND definition_version > 0", name="versions"),
    CheckConstraint(
        " AND ".join(f"{name} >= 0" for name in US_COLUMNS + COUNT_COLUMNS),
        name="non_negative",
    ),
    CheckConstraint(
        "target_transition_count = jsonb_array_length(target_transitions)",
        name="target_transition_count",
    ),
    CheckConstraint(
        "label_transition_count = jsonb_array_length(label_transitions)",
        name="label_transition_count",
    ),
    CheckConstraint(
        f"{SESSION_SIX_TIMES} = session_analysis_us", name="session_composition"
    ),
    CheckConstraint(
        "session_count > 0 OR (session_analysis_us = 0 AND session_lapse_count = 0)",
        name="session_totals",
    ),
)


# Columns the ORM maps on `StoredDailyStats`; the rest hold its nested `stats`.
STORED_STATS_COLUMNS = (
    "account_id",
    "local_date",
    "source_version",
    "definition_version",
    "timezone",
    "computed_at",
)


def map_tables() -> None:
    """Map the analytics entities to their tables once per process.

    `StoredDailyStats.stats` spans flat and JSON columns that no ORM attribute
    can hold, so only its scalar columns are mapped and its repository reads
    and writes whole rows with Core statements.
    """
    mapped = {mapper.class_ for mapper in mapper_registry.mappers}
    if RecomputeMarker not in mapped:
        mapper_registry.map_imperatively(RecomputeMarker, analytics_daily_markers)
    if StoredDailyStats not in mapped:
        mapper_registry.map_imperatively(
            StoredDailyStats,
            analytics_daily_stats,
            include_properties=list(STORED_STATS_COLUMNS),
        )
