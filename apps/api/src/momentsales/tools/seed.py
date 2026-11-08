"""Seed a demo workspace with one user per role (local only, idempotent).

    uv run python -m momentsales.tools.seed          # host, against DATABASE_URL
    docker compose exec api python -m momentsales.tools.seed

Sign in as any of the seeded users with a magic link; in mock mode the link is in
the dev outbox (http://localhost:3000/api/v1/dev/outbox).
A sample research report is added in Phase 2, when reports exist.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from momentsales.core.config import ProviderMode, get_settings
from momentsales.core.db import get_sessionmaker, set_tenant_context
from momentsales.db.billing import BillingInterval, Plan, Subscription, SubscriptionStatus
from momentsales.db.models import Membership, Role, User
from momentsales.modules.workspaces import service as ws_service

DEMO_WORKSPACE = "Demo Candle Co"
DEMO_USERS = {
    Role.OWNER: ("owner@example.com", "Olivia Owner"),
    Role.ADMIN: ("admin@example.com", "Adam Admin"),
    Role.ANALYST: ("analyst@example.com", "Ana Analyst"),
    Role.ACCOUNTANT: ("accountant@example.com", "Alex Accountant"),
}


async def _user(session: AsyncSession, email: str, name: str) -> User:
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, name=name, email_verified_at=datetime.now(UTC))
        session.add(user)
        await session.flush()
    return user


async def seed() -> None:
    settings = get_settings()
    if not settings.is_local:
        raise SystemExit("Refusing to seed outside ENV=local/test.")
    async with get_sessionmaker()() as session, session.begin():
        users = {role: await _user(session, *DEMO_USERS[role]) for role in DEMO_USERS}
        owner = users[Role.OWNER]
        await set_tenant_context(session, user_id=owner.id)
        existing = await session.scalar(
            select(Membership).where(Membership.user_id == owner.id, Membership.role == Role.OWNER)
        )
        if existing is not None:
            print(f"Demo workspace already exists ({existing.workspace_id}).")
            return
        workspace = await ws_service.create_workspace(
            session,
            user=owner,
            name=DEMO_WORKSPACE,
            country="GB",
            enabled_countries=["GB"],
            legal_name="Demo Candle Co Ltd",
            ip_hash=None,
        )
        for role in (Role.ADMIN, Role.ANALYST, Role.ACCOUNTANT):
            membership = Membership(
                workspace_id=workspace.id, user_id=users[role].id, role=role, invited_by=owner.id
            )
            session.add(membership)
            await session.flush()
            await ws_service.log_permission_change(
                session,
                workspace_id=workspace.id,
                actor_id=owner.id,
                action="membership.created",
                description=f"Seeded {users[role].email} as {role.value}",
                object_type="membership",
                object_id=membership.id,
                before=None,
                after={"user_id": str(users[role].id), "role": role.value},
                ip_hash=None,
            )
        # Mock-mode Pro subscription (4 members need more than Growth's 3 seats), as if a
        # trial had already converted. With PROVIDER_MODE=live use a real test checkout.
        await set_tenant_context(session, user_id=owner.id, workspace_id=workspace.id)
        plan = await session.scalar(select(Plan).where(Plan.code == "pro"))
        if plan is not None and settings.provider_mode is ProviderMode.MOCK:
            now = datetime.now(UTC).replace(microsecond=0)
            session.add(
                Subscription(
                    workspace_id=workspace.id,
                    plan_id=plan.id,
                    interval=BillingInterval.MONTH,
                    currency="GBP",
                    status=SubscriptionStatus.ACTIVE,
                    trial_used=True,
                    cancel_at_period_end=False,
                    trial_reminders_sent=[],
                    current_period_start=now,
                    current_period_end=now + timedelta(days=30),
                )
            )
        print(f"Seeded workspace {workspace.name} ({workspace.id}).")
        for role, (email, _) in DEMO_USERS.items():
            print(f"  {role.value:<10} {email}")


def main() -> None:
    if sys.platform == "win32":  # psycopg async needs a selector loop on Windows
        asyncio.run(seed(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(seed())


if __name__ == "__main__":
    main()
