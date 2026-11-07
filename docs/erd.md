# Data model (ERD) — Phase 0

Every table has a UUID (v7) primary key, `created_at`/`updated_at` (`timestamptz`), and, for tenant tables, `workspace_id` with forced RLS (ADR-0004). 🔒 = RLS. 📜 = append-only.

```mermaid
erDiagram
  users ||--o{ memberships : "has"
  users ||--o{ refresh_tokens : "has"
  workspaces ||--o{ memberships : "has"
  workspaces ||--o{ invitations : "has"
  workspaces ||--o{ feature_flag_overrides : "has"
  workspaces ||--o{ touches : "has"
  workspaces ||--o{ audit_log : "chain"
  feature_flags ||--o{ feature_flag_overrides : "overridden by"

  users {
    uuid id PK
    citext email UK
    text name
    timestamptz email_verified_at
    text google_sub UK
    text totp_secret_enc "Fernet"
    timestamptz totp_enabled_at
    bool is_platform_admin
    enum status "active|disabled"
  }
  workspaces {
    uuid id PK "🔒"
    text name
    text slug UK
    text legal_name
    char2 country "GB only (flags)"
    char3 base_currency "GBP"
    jsonb tax_ids
    jsonb settings
    enum status "active|past_due|suspended|deleted"
    uuid created_by FK
  }
  memberships {
    uuid id PK "🔒"
    uuid workspace_id FK
    uuid user_id FK
    enum role "owner|admin|analyst|accountant"
    enum status "active|removed"
    uuid invited_by FK
  }
  invitations {
    uuid id PK "🔒"
    uuid workspace_id FK
    citext email
    enum role
    text token_hash UK "sha256"
    timestamptz expires_at
    timestamptz accepted_at
    timestamptz revoked_at
  }
  touches {
    uuid id PK "🔒📜"
    uuid workspace_id FK
    uuid actor_user_id FK
    enum category "…|team_and_permissions"
    text description
    int minutes
    text object_type
    uuid object_id
    bool auto
  }
  audit_log {
    uuid id PK "🔒📜"
    bigint seq UK
    uuid workspace_id FK "NULL = platform chain"
    uuid actor_user_id FK
    text action
    text object_type
    text object_id
    jsonb before
    jsonb after
    text prev_hash
    text hash
  }
  audit_chain_heads {
    text chain_key PK "workspace id or 'platform'"
    bigint last_seq
    text last_hash
  }
  feature_flags {
    text key PK
    text description
    bool default_enabled
  }
  feature_flag_overrides {
    uuid workspace_id PK "🔒"
    text flag_key PK
    bool enabled
  }
  provider_configs {
    text category PK
    text primary
    text secondary
    text mode "mock|live"
    text failover "auto|manual"
  }
  refresh_tokens {
    uuid id PK
    uuid user_id FK
    uuid family_id
    text token_hash UK
    timestamptz expires_at
    timestamptz revoked_at
    uuid replaced_by_id
  }
  magic_link_tokens {
    uuid id PK
    citext email
    text token_hash UK
    timestamptz expires_at
    timestamptz consumed_at
  }
  waitlist_signups {
    uuid id PK
    citext email UK
    text consent_text_version
    timestamptz consented_at
    text confirm_token_hash UK
    timestamptz confirmed_at
    timestamptz unsubscribed_at
  }
```

Later phases add their entities (ledger, orders, plans, research and so on) with expand-only migrations. Workspace columns such as `stripe_account_id` and `plan` arrive in Phases 1 and 1B.
