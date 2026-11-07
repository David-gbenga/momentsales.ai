from __future__ import annotations

from momentsales.db.models import Role
from momentsales.tenancy.permissions import PERMISSIONS, Action, export_matrix, is_allowed


def test_every_action_has_roles() -> None:
    assert set(PERMISSIONS) == set(Action)
    assert all(PERMISSIONS[a] for a in Action)


def test_owner_can_do_everything() -> None:
    assert all(is_allowed(Role.OWNER, a) for a in Action)


def test_prd_role_rules() -> None:
    # Analyst: run research, export reports, draft listings only (AC-12)
    assert is_allowed(Role.ANALYST, Action.RESEARCH_RUN)
    assert is_allowed(Role.ANALYST, Action.LISTINGS_DRAFT)
    assert not is_allowed(Role.ANALYST, Action.LISTINGS_PUBLISH)
    assert not is_allowed(Role.ANALYST, Action.PAYOUTS_READ)
    assert not is_allowed(Role.ANALYST, Action.LEDGER_WRITE)
    # Accountant: read-only ledger, receipts, sales and exports
    assert is_allowed(Role.ACCOUNTANT, Action.LEDGER_READ)
    assert is_allowed(Role.ACCOUNTANT, Action.LEDGER_EXPORT)
    assert not is_allowed(Role.ACCOUNTANT, Action.LEDGER_WRITE)
    assert not is_allowed(Role.ACCOUNTANT, Action.RESEARCH_RUN)
    # Admin: no billing, no payment disconnect, no workspace delete, no invites
    for action in (
        Action.BILLING_MANAGE,
        Action.INTEGRATIONS_PAYMENTS_MANAGE,
        Action.WORKSPACE_DELETE,
        Action.MEMBERS_INVITE,
    ):
        assert not is_allowed(Role.ADMIN, action)
    assert is_allowed(Role.ADMIN, Action.LISTINGS_PUBLISH)


def test_export_is_json_friendly() -> None:
    matrix = export_matrix()
    assert matrix["listings.publish"] == ["admin", "owner"]
