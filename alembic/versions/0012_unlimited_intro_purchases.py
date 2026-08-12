"""Keep 25-ruble single-generation purchases available without a cap."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_billing_orders_intro_number_matches_product"),
        "billing_orders",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_billing_orders_intro_number_matches_product"),
        "billing_orders",
        "(product_id = 'intro_25' AND intro_number > 0) OR "
        "(product_id <> 'intro_25' AND intro_number IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_billing_orders_intro_number_matches_product"),
        "billing_orders",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_billing_orders_intro_number_matches_product"),
        "billing_orders",
        "(product_id = 'intro_25' AND intro_number BETWEEN 1 AND 3) OR "
        "(product_id <> 'intro_25' AND intro_number IS NULL)",
    )
