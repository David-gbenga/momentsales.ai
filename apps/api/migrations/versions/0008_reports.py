"""Phase 2d reports (ADR-0021): the Top 10 report, its items and share links. Workspace
data with RLS forced. Share links are found by token hash in platform context.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("reports", "report_items", "report_shares")


def _rls_sql() -> str:
    return "\n".join(
        f"""
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY {t}_tenant ON {t} FOR ALL
  USING (workspace_id = app_current_workspace())
  WITH CHECK (workspace_id = app_current_workspace());"""
        for t in TENANT_TABLES
    )


# Reports are immutable; share links can only be revoked (UPDATE), never deleted.
EXTRA_SQL = """
CREATE POLICY report_shares_platform_select ON report_shares FOR SELECT
  USING (app_is_platform());
REVOKE UPDATE, DELETE, TRUNCATE ON reports, report_items FROM app_rw;
REVOKE DELETE, TRUNCATE ON report_shares FROM app_rw;
"""


def _created() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _json(name: str, *, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=nullable)


def _workspace(table: str) -> tuple[sa.Column[object], sa.ForeignKeyConstraint]:
    return (
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f(f"fk_{table}_workspace_id_workspaces")
        ),
    )


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.UUID(), nullable=False),
        *_workspace("reports"),
        sa.Column("run_id", sa.UUID(), nullable=False),
        _json("summary"),
        _json("excluded"),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_reports_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
        sa.UniqueConstraint("run_id", name=op.f("uq_reports_run_id")),
    )
    op.create_index(op.f("ix_reports_workspace_id"), "reports", ["workspace_id"])
    op.create_table(
        "report_items",
        sa.Column("id", sa.UUID(), nullable=False),
        *_workspace("report_items"),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        _json("score_breakdown"),
        sa.Column("why", sa.Text(), nullable=False),
        sa.Column("why_source", sa.String(length=20), nullable=False),
        sa.Column("image_url", sa.String(length=2000), nullable=True),
        _json("best_offer", nullable=True),
        _json("offers"),
        _json("sizes"),
        _json("trend", nullable=True),
        _json("market", nullable=True),
        _json("supplier", nullable=True),
        _json("profit", nullable=True),
        _json("compliance"),
        _json("flags"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_report_items_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_items")),
        sa.UniqueConstraint("report_id", "rank", name="uq_report_items_rank"),
    )
    op.create_index(op.f("ix_report_items_workspace_id"), "report_items", ["workspace_id"])
    op.create_index(op.f("ix_report_items_report_id"), "report_items", ["report_id"])
    op.create_table(
        "report_shares",
        sa.Column("id", sa.UUID(), nullable=False),
        *_workspace("report_shares"),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
            name=op.f("fk_report_shares_report_id_reports"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_report_shares_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_shares")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_report_shares_token_hash")),
    )
    op.create_index(op.f("ix_report_shares_workspace_id"), "report_shares", ["workspace_id"])
    op.create_index(op.f("ix_report_shares_report_id"), "report_shares", ["report_id"])
    op.execute(_rls_sql())
    op.execute(EXTRA_SQL)


def downgrade() -> None:
    op.drop_table("report_shares")
    op.drop_table("report_items")
    op.drop_table("reports")
