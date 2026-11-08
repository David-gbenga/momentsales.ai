"""Phase 1B SaaS billing: plans and prices, subscriptions, usage, credits, invoices.

Global catalogue (no RLS, platform-admin writes in the app): plans, plan_prices,
addon_products. Every other table is tenant data with RLS forced (ADR-0015, ADR-0016).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "subscriptions",
    "usage_counters",
    "run_credits",
    "run_consumptions",
    "addon_purchases",
    "saas_invoices",
    "trial_extensions",
    "cancel_surveys",
    "subscription_pauses",
)
ENUMS = (
    "addon_kind",
    "billing_interval",
    "purchase_status",
    "run_pool",
    "subscription_status",
)


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


# Platform context only (webhook worker, billing sweep, platform-admin API): read across
# workspaces to route an event or list tenants, then switch to the workspace's own context
# to write (ADR-0016).
EXTRA_SQL = """
CREATE POLICY subscriptions_platform_select ON subscriptions FOR SELECT USING (app_is_platform());
CREATE POLICY usage_counters_platform_select ON usage_counters FOR SELECT
  USING (app_is_platform());
CREATE POLICY addon_purchases_platform_select ON addon_purchases FOR SELECT
  USING (app_is_platform());
CREATE POLICY cancel_surveys_platform_select ON cancel_surveys FOR SELECT
  USING (app_is_platform());
CREATE POLICY workspaces_platform_select ON workspaces FOR SELECT USING (app_is_platform());
CREATE POLICY memberships_platform_select ON memberships FOR SELECT USING (app_is_platform());
REVOKE DELETE, TRUNCATE ON subscriptions, saas_invoices, addon_purchases, run_consumptions,
  trial_extensions, cancel_surveys, subscription_pauses FROM app_rw;
"""

DROP_EXTRA_SQL = """
DROP POLICY IF EXISTS workspaces_platform_select ON workspaces;
DROP POLICY IF EXISTS memberships_platform_select ON memberships;
"""

# PRD FR-10 placeholder values (GBP; annual = 10 x monthly). Platform admin edits them
# through the admin API; Stripe Products/Prices come from tools/stripe_billing_seed.py.
# Values the PRD doesn't give (e.g. marketing-kit volume for Starter to Pro) stay null.
PLANS = (
    # code, name, sort, runs, listings, seats, monthly_minor, features
    (
        "lite",
        "Lite",
        1,
        3,
        10,
        1,
        1200,
        {
            "export_level": "monthly_csv",
            "marketing_kits_per_month": 3,
            "auto_approve": False,
            "multi_supplier": False,
            "support": "email",
        },
    ),
    (
        "starter",
        "Starter",
        2,
        10,
        25,
        1,
        2900,
        {
            "export_level": "monthly_csv",
            "marketing_kits_per_month": None,
            "auto_approve": False,
            "multi_supplier": False,
            "support": "email",
        },
    ),
    (
        "growth",
        "Growth",
        3,
        40,
        100,
        3,
        7900,
        {
            "export_level": "csv_pdf_receipts",
            "marketing_kits_per_month": None,
            "auto_approve": True,
            "multi_supplier": True,
            "support": "email_1_business_day",
        },
    ),
    (
        "pro",
        "Pro",
        4,
        120,
        None,
        10,
        19900,
        {
            "export_level": "full_any_period",
            "marketing_kits_per_month": None,
            "auto_approve": True,
            "multi_supplier": True,
            "support": "priority",
        },
    ),
)
ADDONS = (
    ("run_pack", "Research run pack (10 runs)", 10, 1500),
    ("single_report", "Single research report", 1, 900),
)


def _seed() -> None:
    plans = sa.table(
        "plans",
        sa.column("id", sa.UUID()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
        sa.column("research_runs", sa.Integer()),
        sa.column("live_listings", sa.Integer()),
        sa.column("seats", sa.Integer()),
        sa.column("annual_bonus_runs", sa.Integer()),
        sa.column("platform_fee_bps", sa.Integer()),
        sa.column("features", postgresql.JSONB()),
    )
    prices = sa.table(
        "plan_prices",
        sa.column("id", sa.UUID()),
        sa.column("plan_id", sa.UUID()),
        sa.column("currency", sa.String()),
        sa.column("interval", postgresql.ENUM(name="billing_interval", create_type=False)),
        sa.column("amount_minor", sa.BigInteger()),
        sa.column("active", sa.Boolean()),
    )
    addons = sa.table(
        "addon_products",
        sa.column("id", sa.UUID()),
        sa.column("kind", postgresql.ENUM(name="addon_kind", create_type=False)),
        sa.column("name", sa.String()),
        sa.column("runs", sa.Integer()),
        sa.column("currency", sa.String()),
        sa.column("amount_minor", sa.BigInteger()),
        sa.column("active", sa.Boolean()),
    )
    plan_rows, price_rows = [], []
    for code, name, sort, runs, listings, seats, monthly, features in PLANS:
        plan_id = uuid.uuid4()
        plan_rows.append(
            {
                "id": plan_id,
                "code": code,
                "name": name,
                "active": True,
                "sort_order": sort,
                "research_runs": runs,
                "live_listings": listings,
                "seats": seats,
                "annual_bonus_runs": 2,
                "platform_fee_bps": 0,
                "features": features,
            }
        )
        for interval, amount in (("month", monthly), ("year", monthly * 10)):
            price_rows.append(
                {
                    "id": uuid.uuid4(),
                    "plan_id": plan_id,
                    "currency": "GBP",
                    "interval": interval,
                    "amount_minor": amount,
                    "active": True,
                }
            )
    op.bulk_insert(plans, plan_rows)
    op.bulk_insert(prices, price_rows)
    op.bulk_insert(
        addons,
        [
            {
                "id": uuid.uuid4(),
                "kind": kind,
                "name": name,
                "runs": runs,
                "currency": "GBP",
                "amount_minor": amount,
                "active": True,
            }
            for kind, name, runs, amount in ADDONS
        ],
    )


def upgrade() -> None:
    _create_tables()
    op.execute(_rls_sql())
    op.execute(EXTRA_SQL)
    _seed()


def downgrade() -> None:
    op.execute(DROP_EXTRA_SQL)
    _drop_tables()
    for enum_name in ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")


def _create_tables() -> None:
    op.create_table(
        "addon_products",
        sa.Column("kind", sa.Enum("run_pack", "single_report", name="addon_kind"), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("runs", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("stripe_product_id", sa.String(length=100), nullable=True),
        sa.Column("stripe_price_id", sa.String(length=100), nullable=True),
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
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_addon_products_currency_iso3")),
        sa.CheckConstraint(
            "amount_minor > 0 AND runs > 0", name=op.f("ck_addon_products_amount_runs_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_addon_products")),
        sa.UniqueConstraint("kind", "currency", name="uq_addon_products_kind_currency"),
        sa.UniqueConstraint("stripe_price_id", name=op.f("uq_addon_products_stripe_price_id")),
    )
    op.create_table(
        "plans",
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("research_runs", sa.Integer(), nullable=True),
        sa.Column("live_listings", sa.Integer(), nullable=True),
        sa.Column("seats", sa.Integer(), nullable=True),
        sa.Column("annual_bonus_runs", sa.Integer(), nullable=False),
        sa.Column("platform_fee_bps", sa.Integer(), nullable=False),
        sa.Column(
            "features", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("stripe_product_id", sa.String(length=100), nullable=True),
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
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]{1,29}$'", name=op.f("ck_plans_code_format")),
        sa.CheckConstraint(
            "(research_runs IS NULL OR research_runs >= 0) AND (live_listings IS NULL OR live_listings >= 0) AND (seats IS NULL OR seats >= 1) AND annual_bonus_runs >= 0",
            name=op.f("ck_plans_limits_valid"),
        ),
        sa.CheckConstraint(
            "platform_fee_bps BETWEEN 0 AND 5000", name=op.f("ck_plans_platform_fee_bps_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
        sa.UniqueConstraint("code", name=op.f("uq_plans_code")),
    )
    op.create_table(
        "plan_prices",
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("interval", sa.Enum("month", "year", name="billing_interval"), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("stripe_price_id", sa.String(length=100), nullable=True),
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
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_plan_prices_currency_iso3")),
        sa.CheckConstraint("amount_minor > 0", name=op.f("ck_plan_prices_amount_positive")),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["plans.id"], name=op.f("fk_plan_prices_plan_id_plans")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_prices")),
        sa.UniqueConstraint("plan_id", "currency", "interval", name="uq_plan_prices_plan_currency"),
        sa.UniqueConstraint("stripe_price_id", name=op.f("uq_plan_prices_stripe_price_id")),
    )
    op.create_index(op.f("ix_plan_prices_plan_id"), "plan_prices", ["plan_id"], unique=False)
    op.create_table(
        "addon_purchases",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("addon_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM("run_pack", "single_report", name="addon_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("runs", sa.Integer(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "status", sa.Enum("open", "paid", "expired", name="purchase_status"), nullable=False
        ),
        sa.Column("stripe_checkout_session_id", sa.String(length=100), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name=op.f("ck_addon_purchases_currency_iso3")
        ),
        sa.CheckConstraint(
            "quantity > 0 AND runs > 0 AND amount_minor > 0",
            name=op.f("ck_addon_purchases_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["addon_id"],
            ["addon_products.id"],
            name=op.f("fk_addon_purchases_addon_id_addon_products"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_addon_purchases_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_addon_purchases_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_addon_purchases")),
        sa.UniqueConstraint(
            "stripe_checkout_session_id", name=op.f("uq_addon_purchases_stripe_checkout_session_id")
        ),
    )
    op.create_index(
        op.f("ix_addon_purchases_workspace_id"), "addon_purchases", ["workspace_id"], unique=False
    )
    op.create_table(
        "cancel_surveys",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=30), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("offer_made", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "offer_made IN ('pause','discount','none')", name=op.f("ck_cancel_surveys_offer_valid")
        ),
        sa.CheckConstraint(
            "outcome IN ('canceled','accepted_pause','accepted_discount')",
            name=op.f("ck_cancel_surveys_outcome_valid"),
        ),
        sa.CheckConstraint(
            "reason IN ('no_sales','too_expensive','missing_feature','seasonal_break','other')",
            name=op.f("ck_cancel_surveys_reason_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_cancel_surveys_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_cancel_surveys_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cancel_surveys")),
    )
    op.create_index(
        op.f("ix_cancel_surveys_workspace_id"), "cancel_surveys", ["workspace_id"], unique=False
    )
    op.create_table(
        "run_consumptions",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("pool", sa.Enum("plan", "pack", "single", name="run_pool"), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_run_consumptions_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_consumptions")),
        sa.UniqueConstraint("workspace_id", "key", name="uq_run_consumptions_key"),
    )
    op.create_index(
        op.f("ix_run_consumptions_workspace_id"), "run_consumptions", ["workspace_id"], unique=False
    )
    op.create_table(
        "run_credits",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("pack_runs", sa.Integer(), nullable=False),
        sa.Column("single_runs", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "pack_runs >= 0 AND single_runs >= 0", name=op.f("ck_run_credits_credits_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_run_credits_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_credits")),
        sa.UniqueConstraint("workspace_id", name=op.f("uq_run_credits_workspace_id")),
    )
    op.create_table(
        "saas_invoices",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("stripe_invoice_id", sa.String(length=100), nullable=False),
        sa.Column("number", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("tax_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("billing_reason", sa.String(length=50), nullable=True),
        sa.Column("hosted_invoice_url", sa.Text(), nullable=True),
        sa.Column("pdf_url", sa.Text(), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_saas_invoices_currency_iso3")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_saas_invoices_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saas_invoices")),
        sa.UniqueConstraint("stripe_invoice_id", name=op.f("uq_saas_invoices_stripe_invoice_id")),
    )
    op.create_index(
        op.f("ix_saas_invoices_workspace_id"), "saas_invoices", ["workspace_id"], unique=False
    )
    op.create_table(
        "subscription_pauses",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resumes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "months BETWEEN 1 AND 3", name=op.f("ck_subscription_pauses_months_range")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_subscription_pauses_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_subscription_pauses_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscription_pauses")),
    )
    op.create_index(
        op.f("ix_subscription_pauses_workspace_id"),
        "subscription_pauses",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "subscriptions",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("plan_id", sa.UUID(), nullable=False),
        sa.Column(
            "interval",
            postgresql.ENUM("month", "year", name="billing_interval", create_type=False),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "incomplete",
                "trialing",
                "active",
                "past_due",
                "suspended",
                "paused",
                "canceled",
                name="subscription_status",
            ),
            nullable=False,
        ),
        sa.Column("stripe_customer_id", sa.String(length=100), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=100), nullable=True),
        sa.Column("trial_used", sa.Boolean(), nullable=False),
        sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("past_due_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pause_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "trial_reminders_sent",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_subscriptions_currency_iso3")),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["plans.id"], name=op.f("fk_subscriptions_plan_id_plans")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_subscriptions_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        sa.UniqueConstraint(
            "stripe_subscription_id", name=op.f("uq_subscriptions_stripe_subscription_id")
        ),
        sa.UniqueConstraint("workspace_id", name=op.f("uq_subscriptions_workspace_id")),
    )
    op.create_index(
        op.f("ix_subscriptions_stripe_customer_id"),
        "subscriptions",
        ["stripe_customer_id"],
        unique=False,
    )
    op.create_table(
        "trial_extensions",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("applied_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint("days BETWEEN 1 AND 60", name=op.f("ck_trial_extensions_days_range")),
        sa.ForeignKeyConstraint(
            ["applied_by"], ["users.id"], name=op.f("fk_trial_extensions_applied_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_trial_extensions_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trial_extensions")),
    )
    op.create_index(
        op.f("ix_trial_extensions_workspace_id"), "trial_extensions", ["workspace_id"], unique=False
    )
    op.create_table(
        "usage_counters",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("research_runs_used", sa.Integer(), nullable=False),
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
        sa.CheckConstraint(
            "research_runs_used >= 0", name=op.f("ck_usage_counters_runs_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_usage_counters_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_counters")),
        sa.UniqueConstraint("workspace_id", "period_start", name="uq_usage_counters_period"),
    )
    op.create_index(
        op.f("ix_usage_counters_workspace_id"), "usage_counters", ["workspace_id"], unique=False
    )


def _drop_tables() -> None:
    op.drop_index(op.f("ix_usage_counters_workspace_id"), table_name="usage_counters")
    op.drop_table("usage_counters")
    op.drop_index(op.f("ix_trial_extensions_workspace_id"), table_name="trial_extensions")
    op.drop_table("trial_extensions")
    op.drop_index(op.f("ix_subscriptions_stripe_customer_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_subscription_pauses_workspace_id"), table_name="subscription_pauses")
    op.drop_table("subscription_pauses")
    op.drop_index(op.f("ix_saas_invoices_workspace_id"), table_name="saas_invoices")
    op.drop_table("saas_invoices")
    op.drop_table("run_credits")
    op.drop_index(op.f("ix_run_consumptions_workspace_id"), table_name="run_consumptions")
    op.drop_table("run_consumptions")
    op.drop_index(op.f("ix_cancel_surveys_workspace_id"), table_name="cancel_surveys")
    op.drop_table("cancel_surveys")
    op.drop_index(op.f("ix_addon_purchases_workspace_id"), table_name="addon_purchases")
    op.drop_table("addon_purchases")
    op.drop_index(op.f("ix_plan_prices_plan_id"), table_name="plan_prices")
    op.drop_table("plan_prices")
    op.drop_table("plans")
    op.drop_table("addon_products")
