"""Phase 1 ledger and payments: integrations, webhooks, orders, ledger, spends, receipts.

Append-only tables (ADR-0010): ledger_entries, spends, spend_voids, spend_receipts.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07 06:45:20.362413
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "integrations",
    "idempotency_keys",
    "orders",
    "order_items",
    "checkout_sessions",
    "receipts",
    "csv_imports",
    "exports",
)
APPEND_ONLY_TABLES = ("ledger_entries", "spends", "spend_voids", "spend_receipts")
ENUMS = (
    "integration_type",
    "integration_status",
    "order_channel",
    "order_status",
    "checkout_status",
    "ledger_type",
    "spend_category",
    "spend_source",
    "ocr_status",
    "import_kind",
    "job_status",
)


def _rls_sql() -> str:
    parts: list[str] = []
    for t in TENANT_TABLES:
        parts.append(f"""
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY {t}_tenant ON {t} FOR ALL
  USING (workspace_id = app_current_workspace())
  WITH CHECK (workspace_id = app_current_workspace());""")
    for t in APPEND_ONLY_TABLES:
        parts.append(f"""
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY {t}_select ON {t} FOR SELECT USING (workspace_id = app_current_workspace());
CREATE POLICY {t}_insert ON {t} FOR INSERT WITH CHECK (workspace_id = app_current_workspace());
CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t}
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t}
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
REVOKE UPDATE, DELETE, TRUNCATE ON {t} FROM app_rw;""")
    return "\n".join(parts)


# integrations: the webhook worker resolves `account` -> workspace in platform context.
# webhook_events: written and processed only in platform context (ADR-0011); a workspace
# can read its own matched events.
EXTRA_SQL = """
CREATE POLICY integrations_platform_select ON integrations FOR SELECT USING (app_is_platform());
-- receipts: the OCR sweeper finds stuck receipts across workspaces (ids only, then it
-- switches to the receipt's own workspace context to process it).
CREATE POLICY receipts_platform_select ON receipts FOR SELECT USING (app_is_platform());

ALTER TABLE webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_events FORCE ROW LEVEL SECURITY;
CREATE POLICY webhook_events_select ON webhook_events FOR SELECT
  USING (workspace_id = app_current_workspace() OR app_is_platform());
CREATE POLICY webhook_events_insert ON webhook_events FOR INSERT WITH CHECK (app_is_platform());
CREATE POLICY webhook_events_update ON webhook_events FOR UPDATE
  USING (app_is_platform()) WITH CHECK (app_is_platform());
REVOKE DELETE, TRUNCATE ON webhook_events FROM app_rw;
REVOKE DELETE, TRUNCATE ON idempotency_keys, orders, order_items, receipts, exports FROM app_rw;
"""


def upgrade() -> None:
    _create_tables()
    op.execute(_rls_sql())
    op.execute(EXTRA_SQL)


def downgrade() -> None:
    for t in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {t}_no_truncate ON {t}")
        op.execute(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t}")
    _drop_tables()
    for enum_name in ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")


def _create_tables() -> None:
    op.create_table(
        "csv_imports",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Enum("spends", "sales", name="import_kind"), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("column_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rows_total", sa.Integer(), nullable=False),
        sa.Column("rows_imported", sa.Integer(), nullable=False),
        sa.Column("rows_skipped", sa.Integer(), nullable=False),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "completed", "failed", name="job_status"),
            nullable=False,
        ),
        sa.Column("created_by", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_csv_imports_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_csv_imports_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_csv_imports")),
        sa.UniqueConstraint(
            "workspace_id", "kind", "sha256", name=op.f("uq_csv_imports_workspace_id")
        ),
    )
    op.create_index(
        op.f("ix_csv_imports_workspace_id"), "csv_imports", ["workspace_id"], unique=False
    )
    op.create_table(
        "exports",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "running", "completed", "failed", name="job_status"),
            nullable=False,
        ),
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("audit_chain_ok", sa.Boolean(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("period_end >= period_start", name=op.f("ck_exports_period_order")),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], name=op.f("fk_exports_requested_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_exports_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exports")),
    )
    op.create_index(op.f("ix_exports_workspace_id"), "exports", ["workspace_id"], unique=False)
    op.create_table(
        "idempotency_keys",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_idempotency_keys_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("workspace_id", "key", name=op.f("uq_idempotency_keys_workspace_id")),
    )
    op.create_index(
        op.f("ix_idempotency_keys_workspace_id"), "idempotency_keys", ["workspace_id"], unique=False
    )
    op.create_table(
        "integrations",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.Enum("stripe", name="integration_type"), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "restricted", "active", "disconnected", name="integration_status"),
            nullable=False,
        ),
        sa.Column("charges_enabled", sa.Boolean(), nullable=False),
        sa.Column("payouts_enabled", sa.Boolean(), nullable=False),
        sa.Column("details_submitted", sa.Boolean(), nullable=False),
        sa.Column("requirements", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("livemode", sa.Boolean(), nullable=False),
        sa.Column("connected_by", sa.UUID(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["connected_by"], ["users.id"], name=op.f("fk_integrations_connected_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_integrations_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integrations")),
        sa.UniqueConstraint("external_id", name=op.f("uq_integrations_external_id")),
        sa.UniqueConstraint("workspace_id", "type", name=op.f("uq_integrations_workspace_id")),
    )
    op.create_index(
        op.f("ix_integrations_workspace_id"), "integrations", ["workspace_id"], unique=False
    )
    op.create_table(
        "orders",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "channel", sa.Enum("stripe", "manual", "csv", name="order_channel"), nullable=False
        ),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "paid", "partially_refunded", "refunded", "disputed", name="order_status"
            ),
            nullable=False,
        ),
        sa.Column("customer_email", sa.String(length=320), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("gross_minor", sa.BigInteger(), nullable=False),
        sa.Column("tax_minor", sa.BigInteger(), nullable=False),
        sa.Column("fees_minor", sa.BigInteger(), nullable=False),
        sa.Column("refunded_minor", sa.BigInteger(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("charge_id", sa.String(length=100), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.CheckConstraint("gross_minor >= 0", name=op.f("ck_orders_gross_non_negative")),
        sa.CheckConstraint("refunded_minor >= 0", name=op.f("ck_orders_refunded_non_negative")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_orders_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_orders_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint(
            "workspace_id", "channel", "external_id", name=op.f("uq_orders_workspace_id")
        ),
    )
    op.create_index(op.f("ix_orders_workspace_id"), "orders", ["workspace_id"], unique=False)
    op.create_index(
        "ix_orders_workspace_paid_at", "orders", ["workspace_id", "paid_at"], unique=False
    )
    op.create_table(
        "receipts",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("file_key", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("mime", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column(
            "ocr_status",
            sa.Enum("pending", "done", "failed", "skipped", name="ocr_status"),
            nullable=False,
        ),
        sa.Column("ocr_provider", sa.String(length=30), nullable=True),
        sa.Column(
            "ocr_json", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("ocr_error", sa.Text(), nullable=True),
        sa.Column("uploaded_by", sa.UUID(), nullable=False),
        sa.Column("confirmed_by", sa.UUID(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"], name=op.f("fk_receipts_confirmed_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], name=op.f("fk_receipts_uploaded_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_receipts_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_receipts")),
        sa.UniqueConstraint("file_key", name=op.f("uq_receipts_file_key")),
    )
    op.create_index(op.f("ix_receipts_workspace_id"), "receipts", ["workspace_id"], unique=False)
    op.create_index(
        "ix_receipts_workspace_sha256", "receipts", ["workspace_id", "sha256"], unique=False
    )
    op.create_table(
        "webhook_events",
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("account", sa.String(length=100), nullable=True),
        sa.Column("livemode", sa.Boolean(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_webhook_events_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_webhook_events_event_id")),
    )
    op.create_index(
        op.f("ix_webhook_events_workspace_id"), "webhook_events", ["workspace_id"], unique=False
    )
    op.create_table(
        "checkout_sessions",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.Enum("open", "completed", "expired", "failed", name="checkout_status"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_checkout_sessions_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_checkout_sessions_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_checkout_sessions_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checkout_sessions")),
        sa.UniqueConstraint(
            "provider_session_id", name=op.f("uq_checkout_sessions_provider_session_id")
        ),
    )
    op.create_index(
        op.f("ix_checkout_sessions_workspace_id"),
        "checkout_sessions",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "order_items",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("product_key", sa.String(length=64), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "product_key IS NULL OR product_key ~ '^[a-z0-9][a-z0-9-]{0,63}$'",
            name=op.f("ck_order_items_product_key_format"),
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_order_items_quantity_positive")),
        sa.CheckConstraint(
            "unit_amount_minor >= 0", name=op.f("ck_order_items_unit_amount_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_items_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_order_items_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
    )
    op.create_index(op.f("ix_order_items_order_id"), "order_items", ["order_id"], unique=False)
    op.create_index(
        op.f("ix_order_items_workspace_id"), "order_items", ["workspace_id"], unique=False
    )
    op.create_table(
        "spends",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("tax_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("base_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("spent_on", sa.Date(), nullable=False),
        sa.Column("vendor", sa.String(length=255), nullable=False),
        sa.Column(
            "category",
            sa.Enum("stock", "ads", "software", "shipping", "fees", "other", name="spend_category"),
            nullable=False,
        ),
        sa.Column("payment_method", sa.String(length=50), nullable=False),
        sa.Column("product_key", sa.String(length=64), nullable=True),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.Enum("manual", "csv_import", "receipt", name="spend_source"),
            nullable=False,
        ),
        sa.Column("import_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "category <> 'ads' OR product_key IS NOT NULL", name=op.f("ck_spends_ads_need_product")
        ),
        sa.CheckConstraint(
            "product_key IS NULL OR product_key ~ '^[a-z0-9][a-z0-9-]{0,63}$'",
            name=op.f("ck_spends_product_key_format"),
        ),
        sa.CheckConstraint("amount_minor > 0", name=op.f("ck_spends_amount_positive")),
        sa.CheckConstraint("base_amount_minor > 0", name=op.f("ck_spends_base_amount_positive")),
        sa.CheckConstraint(
            "tax_minor >= 0 AND tax_minor <= amount_minor", name=op.f("ck_spends_tax_in_range")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_spends_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["import_id"], ["csv_imports.id"], name=op.f("fk_spends_import_id_csv_imports")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_spends_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_spends_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spends")),
    )
    op.create_index(op.f("ix_spends_workspace_id"), "spends", ["workspace_id"], unique=False)
    op.create_index(
        "ix_spends_workspace_spent_on", "spends", ["workspace_id", "spent_on"], unique=False
    )
    op.create_table(
        "ledger_entries",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "type",
            sa.Enum("sale", "fee", "refund", "payout", "spend", "adjustment", name="ledger_type"),
            nullable=False,
        ),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("spend_id", sa.UUID(), nullable=True),
        sa.Column("product_key", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=False),
        sa.Column("reverses_id", sa.UUID(), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "product_key IS NULL OR product_key ~ '^[a-z0-9][a-z0-9-]{0,63}$'",
            name=op.f("ck_ledger_entries_product_key_format"),
        ),
        sa.CheckConstraint("amount_minor <> 0", name=op.f("ck_ledger_entries_amount_non_zero")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_ledger_entries_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_ledger_entries_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["reverses_id"],
            ["ledger_entries.id"],
            name=op.f("fk_ledger_entries_reverses_id_ledger_entries"),
        ),
        sa.ForeignKeyConstraint(
            ["spend_id"], ["spends.id"], name=op.f("fk_ledger_entries_spend_id_spends")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_ledger_entries_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        sa.UniqueConstraint(
            "workspace_id", "source", "source_ref", name=op.f("uq_ledger_entries_workspace_id")
        ),
    )
    op.create_index(
        op.f("ix_ledger_entries_order_id"), "ledger_entries", ["order_id"], unique=False
    )
    op.create_index(
        op.f("ix_ledger_entries_spend_id"), "ledger_entries", ["spend_id"], unique=False
    )
    op.create_index(
        op.f("ix_ledger_entries_workspace_id"), "ledger_entries", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_ledger_entries_workspace_occurred_on",
        "ledger_entries",
        ["workspace_id", "occurred_on"],
        unique=False,
    )
    op.create_table(
        "spend_receipts",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("spend_id", sa.UUID(), nullable=False),
        sa.Column("receipt_id", sa.UUID(), nullable=False),
        sa.Column("linked_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["linked_by"], ["users.id"], name=op.f("fk_spend_receipts_linked_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"], ["receipts.id"], name=op.f("fk_spend_receipts_receipt_id_receipts")
        ),
        sa.ForeignKeyConstraint(
            ["spend_id"], ["spends.id"], name=op.f("fk_spend_receipts_spend_id_spends")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_spend_receipts_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spend_receipts")),
        sa.UniqueConstraint("spend_id", name=op.f("uq_spend_receipts_spend_id")),
    )
    op.create_index(
        op.f("ix_spend_receipts_workspace_id"), "spend_receipts", ["workspace_id"], unique=False
    )
    op.create_table(
        "spend_voids",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("spend_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("voided_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["spend_id"], ["spends.id"], name=op.f("fk_spend_voids_spend_id_spends")
        ),
        sa.ForeignKeyConstraint(
            ["voided_by"], ["users.id"], name=op.f("fk_spend_voids_voided_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_spend_voids_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spend_voids")),
        sa.UniqueConstraint("spend_id", name=op.f("uq_spend_voids_spend_id")),
    )
    op.create_index(
        op.f("ix_spend_voids_workspace_id"), "spend_voids", ["workspace_id"], unique=False
    )


def _drop_tables() -> None:
    op.drop_index(op.f("ix_spend_voids_workspace_id"), table_name="spend_voids")
    op.drop_table("spend_voids")
    op.drop_index(op.f("ix_spend_receipts_workspace_id"), table_name="spend_receipts")
    op.drop_table("spend_receipts")
    op.drop_index("ix_ledger_entries_workspace_occurred_on", table_name="ledger_entries")
    op.drop_index(op.f("ix_ledger_entries_workspace_id"), table_name="ledger_entries")
    op.drop_index(op.f("ix_ledger_entries_spend_id"), table_name="ledger_entries")
    op.drop_index(op.f("ix_ledger_entries_order_id"), table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_spends_workspace_spent_on", table_name="spends")
    op.drop_index(op.f("ix_spends_workspace_id"), table_name="spends")
    op.drop_table("spends")
    op.drop_index(op.f("ix_order_items_workspace_id"), table_name="order_items")
    op.drop_index(op.f("ix_order_items_order_id"), table_name="order_items")
    op.drop_table("order_items")
    op.drop_index(op.f("ix_checkout_sessions_workspace_id"), table_name="checkout_sessions")
    op.drop_table("checkout_sessions")
    op.drop_index(op.f("ix_webhook_events_workspace_id"), table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index("ix_receipts_workspace_sha256", table_name="receipts")
    op.drop_index(op.f("ix_receipts_workspace_id"), table_name="receipts")
    op.drop_table("receipts")
    op.drop_index("ix_orders_workspace_paid_at", table_name="orders")
    op.drop_index(op.f("ix_orders_workspace_id"), table_name="orders")
    op.drop_table("orders")
    op.drop_index(op.f("ix_integrations_workspace_id"), table_name="integrations")
    op.drop_table("integrations")
    op.drop_index(op.f("ix_idempotency_keys_workspace_id"), table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index(op.f("ix_exports_workspace_id"), table_name="exports")
    op.drop_table("exports")
    op.drop_index(op.f("ix_csv_imports_workspace_id"), table_name="csv_imports")
    op.drop_table("csv_imports")
