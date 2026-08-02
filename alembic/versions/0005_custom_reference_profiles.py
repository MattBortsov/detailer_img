"""Add immutable custom-reference color profiles."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "custom_color_versions",
        sa.Column(
            "color_structure",
            sa.String(16),
            server_default="unspecified",
            nullable=False,
        ),
    )
    op.add_column(
        "custom_color_versions",
        sa.Column(
            "finish",
            sa.String(16),
            server_default="unspecified",
            nullable=False,
        ),
    )
    op.add_column(
        "custom_color_versions",
        sa.Column("analysis_revision", sa.String(32), nullable=True),
    )
    op.add_column(
        "custom_color_versions",
        sa.Column("color_profile", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.create_check_constraint(
        "ck_custom_color_versions_color_structure_supported",
        "custom_color_versions",
        "color_structure IN ('unspecified','solid','multicolor')",
    )
    op.create_check_constraint(
        "ck_custom_color_versions_finish_supported",
        "custom_color_versions",
        "finish IN ('unspecified','matte','satin')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_custom_color_versions_finish_supported",
        "custom_color_versions",
        type_="check",
    )
    op.drop_constraint(
        "ck_custom_color_versions_color_structure_supported",
        "custom_color_versions",
        type_="check",
    )
    op.drop_column("custom_color_versions", "color_profile")
    op.drop_column("custom_color_versions", "analysis_revision")
    op.drop_column("custom_color_versions", "finish")
    op.drop_column("custom_color_versions", "color_structure")
