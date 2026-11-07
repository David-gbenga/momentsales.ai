"""Phase 0 foundations: users, auth tokens, workspaces, memberships, invitations,
feature flags, provider config, waitlist, touches, audit log; RLS, grants, triggers.

Revision ID: 0001
Revises:
Create Date: 2026-11-06 23:58:27.215808
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- Row-level security, grants and append-only guards (ADR-0004, ADR-0008) ---------
# Frozen here on purpose: later changes go in new migrations.

UPGRADE_SQL = """
CREATE FUNCTION app_current_workspace() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT NULLIF(current_setting('app.workspace_id', true), '')::uuid $$;
CREATE FUNCTION app_current_user_id() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;
CREATE FUNCTION app_is_platform() RETURNS boolean LANGUAGE sql STABLE AS
  $$ SELECT coalesce(current_setting('app.platform', true), 'off') = 'on' $$;

CREATE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'insufficient_privilege';
END $$;

-- workspaces: visible in the active workspace, or to any active member (to list "my workspaces")
ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspaces FORCE ROW LEVEL SECURITY;
CREATE POLICY workspaces_select ON workspaces FOR SELECT USING (
  id = app_current_workspace()
  OR id IN (SELECT m.workspace_id FROM memberships m
            WHERE m.user_id = app_current_user_id() AND m.status = 'active')
);
CREATE POLICY workspaces_insert ON workspaces FOR INSERT WITH CHECK (
  id = app_current_workspace() AND created_by = app_current_user_id()
);
CREATE POLICY workspaces_update ON workspaces FOR UPDATE
  USING (id = app_current_workspace()) WITH CHECK (id = app_current_workspace());

-- memberships: the active workspace's rows, plus the signed-in user's own rows
ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY memberships_select ON memberships FOR SELECT USING (
  workspace_id = app_current_workspace() OR user_id = app_current_user_id()
);
CREATE POLICY memberships_insert ON memberships FOR INSERT
  WITH CHECK (workspace_id = app_current_workspace());
CREATE POLICY memberships_update ON memberships FOR UPDATE
  USING (workspace_id = app_current_workspace())
  WITH CHECK (workspace_id = app_current_workspace());

-- invitations, flag overrides: strictly the active workspace
ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE invitations FORCE ROW LEVEL SECURITY;
CREATE POLICY invitations_tenant ON invitations FOR ALL
  USING (workspace_id = app_current_workspace())
  WITH CHECK (workspace_id = app_current_workspace());

ALTER TABLE feature_flag_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE feature_flag_overrides FORCE ROW LEVEL SECURITY;
CREATE POLICY feature_flag_overrides_tenant ON feature_flag_overrides FOR ALL
  USING (workspace_id = app_current_workspace())
  WITH CHECK (workspace_id = app_current_workspace());

-- touches: append-only, active workspace only
ALTER TABLE touches ENABLE ROW LEVEL SECURITY;
ALTER TABLE touches FORCE ROW LEVEL SECURITY;
CREATE POLICY touches_select ON touches FOR SELECT
  USING (workspace_id = app_current_workspace());
CREATE POLICY touches_insert ON touches FOR INSERT
  WITH CHECK (workspace_id = app_current_workspace());
CREATE TRIGGER touches_append_only BEFORE UPDATE OR DELETE ON touches
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER touches_no_truncate BEFORE TRUNCATE ON touches
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

-- audit_log: append-only; a workspace sees its own chain,
-- the platform chain (workspace_id NULL) is visible only in platform context
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_log_select ON audit_log FOR SELECT USING (
  workspace_id = app_current_workspace() OR (workspace_id IS NULL AND app_is_platform())
);
CREATE POLICY audit_log_insert ON audit_log FOR INSERT WITH CHECK (
  workspace_id = app_current_workspace() OR workspace_id IS NULL
);
CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

-- Runtime role (app_rw): DML only, never UPDATE/DELETE on append-only tables
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_rw;
REVOKE UPDATE, DELETE, TRUNCATE ON touches, audit_log FROM app_rw;
REVOKE DELETE, TRUNCATE ON workspaces, memberships FROM app_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO app_rw;
"""

DOWNGRADE_SQL = """
DROP POLICY IF EXISTS workspaces_select ON workspaces;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM app_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM app_rw;
DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;
DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log;
DROP TRIGGER IF EXISTS touches_no_truncate ON touches;
DROP TRIGGER IF EXISTS touches_append_only ON touches;
"""

DOWNGRADE_FUNCTIONS_SQL = """
DROP FUNCTION IF EXISTS forbid_mutation();
DROP FUNCTION IF EXISTS app_is_platform();
DROP FUNCTION IF EXISTS app_current_user_id();
DROP FUNCTION IF EXISTS app_current_workspace();
"""

# (key, description, default_enabled). UK only at launch; Later-roadmap items off.
FEATURE_FLAGS = (
    ("country.GB", "United Kingdom - MVP launch market", True),
    ("country.US", "United States - Later roadmap L3", False),
    ("country.AU", "Australia - Later roadmap L3", False),
    ("country.IE", "Ireland - L3, needs IOSS and EU responsible person", False),
    ("country.DE", "Germany - L3, needs IOSS and EU responsible person", False),
    ("country.FR", "France - L3, needs IOSS and EU responsible person", False),
    ("country.ES", "Spain - L3, needs IOSS and EU responsible person", False),
    ("country.IT", "Italy - L3, needs IOSS and EU responsible person", False),
    ("country.NL", "Netherlands - L3, needs IOSS and EU responsible person", False),
    ("module.audience_research", "Audience research from consented social data - L1", False),
    ("connector.shopify", "Shopify connector and CSV channel import - L2", False),
    ("connector.ads", "Ad-account connectors, Google free listings, Pinterest - L4", False),
    ("fulfilment.own_stock", "Own stock via 3PL - L5", False),
    ("fulfilment.print_on_demand", "Print on demand - L5", False),
    ("platform.mobile_app", "Native mobile app - L6", False),
    ("growth.referrals", "Referral programme - L7", False),
    ("store.custom_domains", "White-label custom domains - L7", False),
)


def _seed_feature_flags() -> None:
    table = sa.table(
        "feature_flags",
        sa.column("key", sa.String),
        sa.column("description", sa.Text),
        sa.column("default_enabled", sa.Boolean),
    )
    op.bulk_insert(
        table,
        [{"key": k, "description": d, "default_enabled": e} for k, d, e in FEATURE_FLAGS],
    )


def _create_tables() -> None:
    op.create_table(
        "audit_chain_heads",
        sa.Column("chain_key", sa.String(length=36), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=True),
        sa.Column("last_hash", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("chain_key", name=op.f("pk_audit_chain_heads")),
    )
    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("default_enabled", sa.Boolean(), nullable=False),
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
        sa.PrimaryKeyConstraint("key", name=op.f("pk_feature_flags")),
    )
    op.create_table(
        "magic_link_tokens",
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_ip_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_magic_link_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_magic_link_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_magic_link_tokens_email"), "magic_link_tokens", ["email"], unique=False
    )
    op.create_table(
        "provider_configs",
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("primary", sa.String(length=50), nullable=False),
        sa.Column("secondary", sa.String(length=50), nullable=True),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("failover", sa.String(length=10), nullable=False),
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
            "failover IN ('auto','manual')", name=op.f("ck_provider_configs_failover")
        ),
        sa.CheckConstraint("mode IN ('mock','live')", name=op.f("ck_provider_configs_mode")),
        sa.PrimaryKeyConstraint("category", name=op.f("pk_provider_configs")),
    )
    op.create_table(
        "users",
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("google_sub", sa.String(length=255), nullable=True),
        sa.Column("totp_secret_enc", sa.Text(), nullable=True),
        sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_platform_admin", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "disabled", name="user_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.UniqueConstraint("google_sub", name=op.f("uq_users_google_sub")),
    )
    op.create_table(
        "waitlist_signups",
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("consent_text_version", sa.String(length=50), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirm_token_hash", sa.String(length=64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_waitlist_signups")),
        sa.UniqueConstraint(
            "confirm_token_hash", name=op.f("uq_waitlist_signups_confirm_token_hash")
        ),
        sa.UniqueConstraint("email", name=op.f("uq_waitlist_signups_email")),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.UUID(), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_refresh_tokens_family_id"), "refresh_tokens", ["family_id"], unique=False
    )
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False)
    op.create_table(
        "workspaces",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column(
            "tax_ids", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "settings", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum("active", "past_due", "suspended", "deleted", name="workspace_status"),
            server_default="active",
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
        sa.CheckConstraint(
            "base_currency ~ '^[A-Z]{3}$'", name=op.f("ck_workspaces_currency_iso3")
        ),
        sa.CheckConstraint("country ~ '^[A-Z]{2}$'", name=op.f("ck_workspaces_country_iso2")),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'", name=op.f("ck_workspaces_slug_format")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_workspaces_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.UniqueConstraint("slug", name=op.f("uq_workspaces_slug")),
    )
    op.create_table(
        "audit_log",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("object_type", sa.String(length=50), nullable=False),
        sa.Column("object_id", sa.String(length=100), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("prev_hash", sa.String(length=64), nullable=True),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_audit_log_actor_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_audit_log_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
        sa.UniqueConstraint("seq", name=op.f("uq_audit_log_seq")),
    )
    op.create_index(op.f("ix_audit_log_workspace_id"), "audit_log", ["workspace_id"], unique=False)
    op.create_table(
        "feature_flag_overrides",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("flag_key", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("set_by", sa.UUID(), nullable=True),
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
            ["flag_key"],
            ["feature_flags.key"],
            name=op.f("fk_feature_flag_overrides_flag_key_feature_flags"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"], ["users.id"], name=op.f("fk_feature_flag_overrides_set_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_feature_flag_overrides_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "flag_key", name=op.f("pk_feature_flag_overrides")),
    )
    op.create_table(
        "invitations",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("owner", "admin", "analyst", "accountant", name="workspace_role"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by", sa.UUID(), nullable=False),
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
            ["invited_by"], ["users.id"], name=op.f("fk_invitations_invited_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_invitations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invitations_token_hash")),
    )
    op.create_index(
        op.f("ix_invitations_workspace_id"), "invitations", ["workspace_id"], unique=False
    )
    op.create_table(
        "memberships",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("owner", "admin", "analyst", "accountant", name="workspace_role"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("active", "removed", name="membership_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column("invited_by", sa.UUID(), nullable=True),
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
            ["invited_by"], ["users.id"], name=op.f("fk_memberships_invited_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memberships_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_memberships_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_memberships_ws_user"),
    )
    op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_memberships_workspace_id"), "memberships", ["workspace_id"], unique=False
    )
    op.create_table(
        "touches",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "research_edit",
                "listing_edit",
                "pricing",
                "fulfilment",
                "customer_service",
                "agent_override",
                "team_and_permissions",
                name="touch_category",
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=50), nullable=True),
        sa.Column("object_id", sa.UUID(), nullable=True),
        sa.Column("auto", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint("minutes >= 0", name=op.f("ck_touches_minutes_non_negative")),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_touches_actor_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_touches_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_touches")),
    )
    op.create_index(op.f("ix_touches_workspace_id"), "touches", ["workspace_id"], unique=False)


def _drop_tables() -> None:
    op.drop_index(op.f("ix_touches_workspace_id"), table_name="touches")
    op.drop_table("touches")
    op.drop_index(op.f("ix_memberships_workspace_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_table("memberships")
    op.drop_index(op.f("ix_invitations_workspace_id"), table_name="invitations")
    op.drop_table("invitations")
    op.drop_table("feature_flag_overrides")
    op.drop_index(op.f("ix_audit_log_workspace_id"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("workspaces")
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_family_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("waitlist_signups")
    op.drop_table("users")
    op.drop_table("provider_configs")
    op.drop_index(op.f("ix_magic_link_tokens_email"), table_name="magic_link_tokens")
    op.drop_table("magic_link_tokens")
    op.drop_table("feature_flags")
    op.drop_table("audit_chain_heads")


def upgrade() -> None:
    _create_tables()
    op.execute(UPGRADE_SQL)
    _seed_feature_flags()


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
    _drop_tables()
    op.execute(DOWNGRADE_FUNCTIONS_SQL)
    for enum_name in (
        "touch_category",
        "membership_status",
        "workspace_role",
        "workspace_status",
        "user_status",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
