"""Persist minimal Telegram audience records and report deliveries."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_users",
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "telegram_user_id > 0", name="ck_telegram_users_id_positive"
        ),
        sa.CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_telegram_users_seen_order",
        ),
        sa.PrimaryKeyConstraint("telegram_user_id", name="pk_telegram_users"),
    )
    op.create_table(
        "daily_stats_deliveries",
        sa.Column("report_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "telegram_user_id > 0",
            name="ck_daily_stats_deliveries_id_positive",
        ),
        sa.PrimaryKeyConstraint(
            "report_date", "telegram_user_id", name="pk_daily_stats_deliveries"
        ),
    )
    op.execute(
        """
        INSERT INTO telegram_users (telegram_user_id, first_seen_at, last_seen_at)
        SELECT telegram_user_id, min(seen_at), max(seen_at)
        FROM (
            SELECT telegram_user_id, accepted_at AS seen_at FROM active_sources
            UNION ALL
            SELECT telegram_user_id, created_at AS seen_at FROM mini_app_sessions
            UNION ALL
            SELECT telegram_user_id, created_at AS seen_at FROM generation_jobs
            UNION ALL
            SELECT telegram_user_id, created_at AS seen_at FROM custom_colors
        ) known_users
        GROUP BY telegram_user_id
        ON CONFLICT (telegram_user_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("daily_stats_deliveries")
    op.drop_table("telegram_users")
