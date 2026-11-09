"""Role → action matrix (PRD "User roles and permissions").

The single source of truth. The API enforces it server-side; the web app imports
the exported copy (packages/shared/src/generated/permissions.json) only to hide or
lock controls the API already forbids.

Actions for later phases are listed now so the UI can gate menus consistently;
each is enforced by its endpoint when that phase builds it.
"""

from __future__ import annotations

from enum import StrEnum

from momentsales.db.models import Role

OW, AD, AN, AC = Role.OWNER, Role.ADMIN, Role.ANALYST, Role.ACCOUNTANT


class Action(StrEnum):
    # Phase 0
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_UPDATE = "workspace.update"
    WORKSPACE_DELETE = "workspace.delete"
    MEMBERS_READ = "members.read"
    MEMBERS_INVITE = "members.invite"
    MEMBERS_CHANGE_ROLE = "members.change_role"
    MEMBERS_REMOVE = "members.remove"
    FLAGS_READ = "flags.read"
    # Later phases (enforced when built)
    RESEARCH_RUN = "research.run"
    REPORTS_READ = "reports.read"
    REPORTS_EXPORT = "reports.export"
    LISTINGS_DRAFT = "listings.draft"
    LISTINGS_PUBLISH = "listings.publish"
    STORE_MANAGE = "store.manage"
    ORDERS_READ = "orders.read"
    FULFILMENT_APPROVE = "fulfilment.approve"
    PAYOUTS_READ = "payouts.read"
    LEDGER_READ = "ledger.read"
    LEDGER_WRITE = "ledger.write"
    LEDGER_EXPORT = "ledger.export"
    RECEIPTS_UPLOAD = "receipts.upload"
    INTEGRATIONS_PAYMENTS_MANAGE = "integrations.payments.manage"
    BILLING_MANAGE = "billing.manage"
    BILLING_USAGE_READ = "billing.usage.read"
    # Phase 1
    CHECKOUT_TEST = "checkout.test"
    SALES_RECORD = "sales.record"
    TOUCHES_WRITE = "touches.write"
    # Phase 1B
    BILLING_INVOICES_READ = "billing.invoices.read"
    # Phase 2c: connect a seller's own supplier account (Avasam)
    INTEGRATIONS_SUPPLIERS_MANAGE = "integrations.suppliers.manage"
    # Phase 2d: shareable read-only report links
    REPORTS_SHARE = "reports.share"


PERMISSIONS: dict[Action, frozenset[Role]] = {
    Action.WORKSPACE_READ: frozenset({OW, AD, AN, AC}),
    Action.WORKSPACE_UPDATE: frozenset({OW}),
    Action.WORKSPACE_DELETE: frozenset({OW}),
    Action.MEMBERS_READ: frozenset({OW, AD}),
    Action.MEMBERS_INVITE: frozenset({OW}),
    Action.MEMBERS_CHANGE_ROLE: frozenset({OW}),
    Action.MEMBERS_REMOVE: frozenset({OW}),
    Action.FLAGS_READ: frozenset({OW, AD, AN, AC}),
    Action.RESEARCH_RUN: frozenset({OW, AD, AN}),
    Action.REPORTS_READ: frozenset({OW, AD, AN}),
    Action.REPORTS_EXPORT: frozenset({OW, AD, AN}),
    Action.LISTINGS_DRAFT: frozenset({OW, AD, AN}),
    Action.LISTINGS_PUBLISH: frozenset({OW, AD}),
    Action.STORE_MANAGE: frozenset({OW, AD}),
    Action.ORDERS_READ: frozenset({OW, AD, AC}),
    Action.FULFILMENT_APPROVE: frozenset({OW, AD}),
    Action.PAYOUTS_READ: frozenset({OW, AD, AC}),
    Action.LEDGER_READ: frozenset({OW, AD, AC}),
    Action.LEDGER_WRITE: frozenset({OW, AD}),
    Action.LEDGER_EXPORT: frozenset({OW, AC}),
    Action.RECEIPTS_UPLOAD: frozenset({OW, AD}),
    Action.INTEGRATIONS_PAYMENTS_MANAGE: frozenset({OW}),
    Action.BILLING_MANAGE: frozenset({OW}),
    Action.BILLING_USAGE_READ: frozenset({OW, AD}),
    Action.CHECKOUT_TEST: frozenset({OW, AD}),
    Action.SALES_RECORD: frozenset({OW, AD}),
    Action.TOUCHES_WRITE: frozenset({OW, AD}),
    # PRD: only the Owner sees billing details; Admins see usage and limits.
    Action.BILLING_INVOICES_READ: frozenset({OW}),
    Action.INTEGRATIONS_SUPPLIERS_MANAGE: frozenset({OW, AD}),
    Action.REPORTS_SHARE: frozenset({OW, AD, AN}),
}

assert set(PERMISSIONS) == set(Action), "every action needs a role set"


def is_allowed(role: Role, action: Action) -> bool:
    return role in PERMISSIONS[action]


def export_matrix() -> dict[str, list[str]]:
    return {a.value: sorted(r.value for r in roles) for a, roles in PERMISSIONS.items()}
