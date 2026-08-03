"""Allow audited administrator edits of custom color details."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_admin_audit_events_action_supported",
        "admin_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_admin_audit_events_action_supported",
        "admin_audit_events",
        "action IN ('approve','reject','rename','edit','hide','restore','delete')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_admin_audit_events_action_supported",
        "admin_audit_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_admin_audit_events_action_supported",
        "admin_audit_events",
        "action IN ('approve','reject','rename','hide','restore','delete')",
    )
