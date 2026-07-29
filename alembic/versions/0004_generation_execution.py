"""Add bounded generation attempts, leases, and delivery receipts."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("generation_jobs", sa.Column("lease_owner", sa.String(64)))
    op.add_column(
        "generation_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "generation_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "generation_jobs", sa.Column("terminal_at", sa.DateTime(timezone=True))
    )
    op.add_column("generation_jobs", sa.Column("result_message_id", sa.BigInteger()))
    op.add_column(
        "generation_jobs",
        sa.Column("custom_reference_released_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_generation_jobs_attempt_count_nonnegative",
        "generation_jobs",
        "attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_generation_jobs_lease_matches_running",
        "generation_jobs",
        "(status = 'running' AND lease_owner IS NOT NULL "
        "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL) "
        "OR (status <> 'running' AND lease_owner IS NULL "
        "AND lease_expires_at IS NULL AND heartbeat_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_generation_jobs_terminal_time_matches_status",
        "generation_jobs",
        "(status IN ('succeeded', 'failed') AND terminal_at IS NOT NULL) "
        "OR (status IN ('queued', 'running') AND terminal_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_generation_jobs_result_message_matches_success",
        "generation_jobs",
        "(status = 'succeeded' AND result_message_id > 0) "
        "OR (status <> 'succeeded' AND result_message_id IS NULL)",
    )

    op.create_table(
        "generation_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column(
            "safe_preupload_retries",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("provider_started_at", sa.DateTime(timezone=True)),
        sa.Column("provider_completed_at", sa.DateTime(timezone=True)),
        sa.Column("provider_name", sa.String(64)),
        sa.Column("provider_request_id", sa.String(128)),
        sa.Column("provider_status_code", sa.Integer()),
        sa.Column("provider_latency_ms", sa.BigInteger()),
        sa.Column("input_tokens", sa.BigInteger()),
        sa.Column("output_tokens", sa.BigInteger()),
        sa.Column("total_tokens", sa.BigInteger()),
        sa.Column("cost_usd", sa.Numeric(12, 6)),
        sa.Column("output_byte_count", sa.BigInteger()),
        sa.Column("output_width", sa.Integer()),
        sa.Column("output_height", sa.Integer()),
        sa.Column("output_format", sa.String(8)),
        sa.Column("output_sha256", sa.String(64)),
        sa.Column("delivery_started_at", sa.DateTime(timezone=True)),
        sa.Column("result_message_id", sa.BigInteger()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_summary", sa.String(240)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "attempt_number > 0",
            name="ck_generation_attempts_attempt_number_positive",
        ),
        sa.CheckConstraint(
            "worker_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$'",
            name="ck_generation_attempts_worker_id_safe",
        ),
        sa.CheckConstraint(
            "state IN ('claimed', 'source_ready', 'provider_started', "
            "'provider_succeeded', 'delivering', 'succeeded', 'failed', "
            "'ambiguous')",
            name="ck_generation_attempts_state_supported",
        ),
        sa.CheckConstraint(
            "safe_preupload_retries BETWEEN 0 AND 1",
            name="ck_generation_attempts_safe_preupload_retries_range",
        ),
        sa.CheckConstraint(
            "provider_status_code IS NULL OR provider_status_code BETWEEN 100 AND 599",
            name="ck_generation_attempts_provider_status_code_range",
        ),
        sa.CheckConstraint(
            "provider_latency_ms IS NULL OR provider_latency_ms >= 0",
            name="ck_generation_attempts_provider_latency_nonnegative",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_generation_attempts_input_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_generation_attempts_output_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="ck_generation_attempts_total_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0",
            name="ck_generation_attempts_cost_nonnegative",
        ),
        sa.CheckConstraint(
            "output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_generation_attempts_output_sha256_hex",
        ),
        sa.CheckConstraint(
            "(state = 'succeeded' AND result_message_id > 0 "
            "AND completed_at IS NOT NULL) "
            "OR (state <> 'succeeded' AND result_message_id IS NULL)",
            name="ck_generation_attempts_receipt_matches_success",
        ),
        sa.CheckConstraint(
            "(state IN ('failed', 'ambiguous') AND error_code IS NOT NULL "
            "AND completed_at IS NOT NULL) "
            "OR (state NOT IN ('failed', 'ambiguous') "
            "AND error_code IS NULL AND error_summary IS NULL)",
            name="ck_generation_attempts_error_matches_terminal_state",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_generation_attempts_error_code_safe",
        ),
        sa.CheckConstraint(
            "updated_at >= started_at",
            name="ck_generation_attempts_updated_after_started",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["generation_jobs.id"],
            name="fk_generation_attempts_job_id_generation_jobs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_generation_attempts"),
        sa.UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_generation_attempts_job_id",
        ),
    )
    op.create_index(
        "uq_generation_attempts_one_provider_start",
        "generation_attempts",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("provider_started_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_generation_attempts_one_provider_start",
        table_name="generation_attempts",
    )
    op.drop_table("generation_attempts")
    op.drop_constraint(
        "ck_generation_jobs_result_message_matches_success",
        "generation_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_generation_jobs_terminal_time_matches_status",
        "generation_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_generation_jobs_lease_matches_running",
        "generation_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_generation_jobs_attempt_count_nonnegative",
        "generation_jobs",
        type_="check",
    )
    op.drop_column("generation_jobs", "custom_reference_released_at")
    op.drop_column("generation_jobs", "result_message_id")
    op.drop_column("generation_jobs", "terminal_at")
    op.drop_column("generation_jobs", "heartbeat_at")
    op.drop_column("generation_jobs", "lease_expires_at")
    op.drop_column("generation_jobs", "lease_owner")
    op.drop_column("generation_jobs", "attempt_count")
