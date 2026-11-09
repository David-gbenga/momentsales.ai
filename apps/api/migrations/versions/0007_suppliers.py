"""Phase 2c suppliers (ADR-0020): the global `suppliers` reference table, append-only
`supplier_verifications` (Verified UK supplier rule evidence), and encrypted seller
credentials on `integrations` for Avasam.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-09
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUPPLIERS = [
    {
        "provider": "cj",
        "name": "CJ Dropshipping",
        "warehouse_countries": ["GB"],
        "notes": "UK warehouse stock only (FR-11). GB stock to confirm with CJ.",
    },
    {
        "provider": "avasam",
        "name": "Avasam",
        "warehouse_countries": ["GB"],
        "notes": "Per seller: each workspace connects its own keys; never cached across "
        "workspaces.",
    },
]

CHECKS = ("dropship_no_moq", "auto_stock_cost", "tracked_uk", "uk_returns")


def upgrade() -> None:
    op.execute("ALTER TYPE integration_type ADD VALUE IF NOT EXISTS 'avasam'")
    op.add_column("integrations", sa.Column("credentials_encrypted", sa.Text(), nullable=True))
    op.create_table(
        "suppliers",
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("warehouse_countries", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("returns_policy_url", sa.String(length=2000), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppliers")),
        sa.UniqueConstraint("provider", name=op.f("uq_suppliers_provider")),
    )
    op.create_table(
        "supplier_verifications",
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("check", sa.Enum(*CHECKS, name="verification_check"), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("evidence_url", sa.String(length=2000), nullable=False),
        sa.Column("evidence_note", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["verified_by"], ["users.id"], name=op.f("fk_supplier_verifications_verified_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_verifications")),
    )
    op.create_index(
        "ix_supplier_verifications_lookup",
        "supplier_verifications",
        ["provider", "check", "verified_at"],
    )
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON supplier_verifications FROM app_rw")
    table = sa.table(
        "suppliers",
        sa.column("id", sa.UUID()),
        sa.column("provider", sa.String()),
        sa.column("name", sa.String()),
        sa.column("warehouse_countries", postgresql.JSONB()),
        sa.column("notes", sa.Text()),
    )
    op.bulk_insert(table, [{"id": uuid.uuid4(), **row} for row in SUPPLIERS])


def downgrade() -> None:
    op.drop_index("ix_supplier_verifications_lookup", table_name="supplier_verifications")
    op.drop_table("supplier_verifications")
    op.execute("DROP TYPE IF EXISTS verification_check")
    op.drop_table("suppliers")
    op.drop_column("integrations", "credentials_encrypted")
    # Postgres cannot drop an enum value; 'avasam' stays in integration_type.
