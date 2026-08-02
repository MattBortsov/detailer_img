"""Add gloss to supported custom-reference finishes."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_custom_color_versions_finish_supported",
        "custom_color_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_custom_color_versions_finish_supported",
        "custom_color_versions",
        "finish IN ('unspecified','matte','satin','gloss')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_custom_color_versions_finish_supported",
        "custom_color_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_custom_color_versions_finish_supported",
        "custom_color_versions",
        "finish IN ('unspecified','matte','satin')",
    )
