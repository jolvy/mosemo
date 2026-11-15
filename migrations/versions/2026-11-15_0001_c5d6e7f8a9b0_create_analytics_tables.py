"""Create the analytics daily stats, recompute markers and definition row (M2).

The definition row starts at version 1, the `CURRENT_DEFINITION.version` of the
code. The claim index predicate `attempts < 6` is the `RetryPolicy` quarantine
threshold (ADR 0016 「실패·재처리」).

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None

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


def _account_id(table: str) -> sa.Column:
    return sa.Column(
        "account_id",
        sa.Uuid(),
        sa.ForeignKey(
            "accounts.id", name=op.f(f"{table}_account_id_fkey"), ondelete="CASCADE"
        ),
        nullable=False,
    )


def upgrade() -> None:
    op.execute(
        sa.schema.CreateSequence(
            sa.Sequence("analytics_daily_marker_version_seq", data_type=sa.BigInteger)
        )
    )
    definitions = op.create_table(
        "analytics_definitions",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("analytics_definitions_id_check")),
        sa.CheckConstraint(
            "version > 0", name=op.f("analytics_definitions_version_check")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("analytics_definitions_pkey")),
    )
    assert definitions is not None
    op.execute(definitions.insert().values(id=1, version=1, changed_at=sa.func.now()))
    op.create_table(
        "analytics_daily_markers",
        _account_id("analytics_daily_markers"),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('source', 'definition')",
            name=op.f("analytics_daily_markers_kind_check"),
        ),
        sa.CheckConstraint(
            "attempts >= 0", name=op.f("analytics_daily_markers_attempts_check")
        ),
        sa.CheckConstraint(
            "(last_error IS NULL) = (last_failed_at IS NULL)",
            name=op.f("analytics_daily_markers_last_failure_check"),
        ),
        sa.PrimaryKeyConstraint(
            "account_id", "local_date", name=op.f("analytics_daily_markers_pkey")
        ),
    )
    op.create_index(
        "analytics_daily_markers_claim_idx",
        "analytics_daily_markers",
        [sa.text("(kind = 'definition')"), "version"],
        postgresql_where=sa.text("attempts < 6"),
    )
    op.create_table(
        "analytics_daily_stats",
        _account_id("analytics_daily_stats"),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("source_version", sa.BigInteger(), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.String(length=255), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        *(sa.Column(name, sa.BigInteger(), nullable=False) for name in US_COLUMNS),
        *(sa.Column(name, sa.Integer(), nullable=False) for name in COUNT_COLUMNS),
        *(sa.Column(name, postgresql.JSONB(), nullable=False) for name in JSON_COLUMNS),
        sa.CheckConstraint(
            "source_version > 0 AND definition_version > 0",
            name=op.f("analytics_daily_stats_versions_check"),
        ),
        sa.CheckConstraint(
            " AND ".join(f"{name} >= 0" for name in US_COLUMNS + COUNT_COLUMNS),
            name=op.f("analytics_daily_stats_non_negative_check"),
        ),
        sa.CheckConstraint(
            "target_transition_count = jsonb_array_length(target_transitions)",
            name=op.f("analytics_daily_stats_target_transition_count_check"),
        ),
        sa.CheckConstraint(
            "label_transition_count = jsonb_array_length(label_transitions)",
            name=op.f("analytics_daily_stats_label_transition_count_check"),
        ),
        sa.CheckConstraint(
            f"{SESSION_SIX_TIMES} = session_analysis_us",
            name=op.f("analytics_daily_stats_session_composition_check"),
        ),
        sa.CheckConstraint(
            "session_count > 0 OR (session_analysis_us = 0 AND session_lapse_count = 0)",
            name=op.f("analytics_daily_stats_session_totals_check"),
        ),
        sa.PrimaryKeyConstraint(
            "account_id", "local_date", name=op.f("analytics_daily_stats_pkey")
        ),
    )


def downgrade() -> None:
    op.drop_table("analytics_daily_stats")
    op.drop_index("analytics_daily_markers_claim_idx", "analytics_daily_markers")
    op.drop_table("analytics_daily_markers")
    op.drop_table("analytics_definitions")
    op.execute(
        sa.schema.DropSequence(sa.Sequence("analytics_daily_marker_version_seq"))
    )
