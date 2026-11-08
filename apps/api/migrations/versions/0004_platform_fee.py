"""Phase 1B follow-up: platform fee on seller sales (ADR-0017).

Adds the application fee to checkout sessions and orders, and sets Lite's rate to 2%
(product-owner decision, PRD FR-10). No new tables, so RLS is unchanged.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LITE_FEE_BPS = 200


def upgrade() -> None:
    op.add_column(
        "checkout_sessions",
        sa.Column("application_fee_minor", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "orders",
        sa.Column("platform_fee_minor", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "orders",
        sa.Column(
            "platform_fee_refunded_minor", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    op.add_column("orders", sa.Column("platform_fee_id", sa.String(length=100), nullable=True))
    op.create_check_constraint(
        op.f("ck_checkout_sessions_application_fee_non_negative"),
        "checkout_sessions",
        "application_fee_minor >= 0",
    )
    op.create_check_constraint(
        op.f("ck_orders_platform_fee_refund_range"),
        "orders",
        "platform_fee_minor >= 0 AND platform_fee_refunded_minor BETWEEN 0 AND platform_fee_minor",
    )
    op.execute(
        sa.text("UPDATE plans SET platform_fee_bps = :bps WHERE code = 'lite'").bindparams(
            bps=LITE_FEE_BPS
        )
    )


def downgrade() -> None:
    op.execute(sa.text("UPDATE plans SET platform_fee_bps = 0 WHERE code = 'lite'"))
    op.drop_constraint(op.f("ck_orders_platform_fee_refund_range"), "orders", type_="check")
    op.drop_constraint(
        op.f("ck_checkout_sessions_application_fee_non_negative"),
        "checkout_sessions",
        type_="check",
    )
    op.drop_column("orders", "platform_fee_id")
    op.drop_column("orders", "platform_fee_refunded_minor")
    op.drop_column("orders", "platform_fee_minor")
    op.drop_column("checkout_sessions", "application_fee_minor")
