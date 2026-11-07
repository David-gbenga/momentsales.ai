"""Auth endpoints (ADR-0003).

Browser sessions: the access token is returned in the body (kept in memory by the
web app); the refresh token is an httpOnly, SameSite=Strict cookie scoped to
/api/v1/auth. State-changing cookie endpoints also require the custom header
`X-Requested-With: momentsales`, which a cross-site page cannot send without CORS.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Cookie, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from momentsales.adapters.email.base import EmailMessage
from momentsales.adapters.identity.base import IdentityError
from momentsales.core.config import Settings
from momentsales.core.errors import PROBLEM_JSON, ProblemError, unauthorized
from momentsales.core.ratelimit import limit
from momentsales.core.security import (
    decode_token,
    decrypt_secret,
    encrypt_secret,
    hash_ip,
    hash_token,
    issue_purpose_token,
    new_totp_secret,
    sign,
    totp_uri,
    unsign,
    verify_totp,
)
from momentsales.db.models import Membership, MembershipStatus, RefreshToken, User, Workspace
from momentsales.deps import AppSettings, Registry, send_email
from momentsales.modules.audit import service as audit
from momentsales.modules.auth import service
from momentsales.modules.providers.registry import ProviderConfigError
from momentsales.tenancy.deps import CurrentPrincipal, DbSession, client_ip

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "ms_refresh"
REFRESH_PATH = "/api/v1/auth"
OAUTH_COOKIE = "ms_oauth"
OAUTH_PATH = "/api/v1/auth/google"
CSRF_HEADER_VALUE = "momentsales"
MFA_TTL_SECONDS = 300


# --- Schemas -------------------------------------------------------------------------


class MagicLinkIn(BaseModel):
    email: EmailStr


class AcceptedOut(BaseModel):
    status: Literal["check_your_email"] = "check_your_email"


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=500)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    totp_enabled: bool
    is_platform_admin: bool


class SessionOut(BaseModel):
    status: Literal["signed_in"] = "signed_in"
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int
    user: UserOut


class MfaRequiredOut(BaseModel):
    status: Literal["mfa_required"] = "mfa_required"
    mfa_token: str


class MfaChallengeIn(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=6)


class TotpEnrollOut(BaseModel):
    secret: str
    otpauth_uri: str


class TotpCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class MembershipOut(BaseModel):
    workspace_id: uuid.UUID
    workspace_name: str
    workspace_slug: str
    role: str


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]


# --- Helpers -------------------------------------------------------------------------


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        totp_enabled=user.totp_enabled_at is not None,
        is_platform_admin=user.is_platform_admin,
    )


def _set_refresh_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_ttl_days * 86400,
        path=REFRESH_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def require_csrf_header(
    x_requested_with: Annotated[str | None, Header()] = None,
) -> None:
    if x_requested_with != CSRF_HEADER_VALUE:
        raise ProblemError(403, "csrf_failed", "Request blocked", "Missing X-Requested-With.")


def _session_out(issued: service.IssuedSession, settings: Settings) -> SessionOut:
    return SessionOut(
        access_token=issued.access_token,
        expires_in=settings.access_token_ttl_seconds,
        user=user_out(issued.user),
    )


async def _finish_sign_in(
    request: Request,
    response: Response,
    session: DbSession,
    settings: Settings,
    user: User,
    method: str,
) -> SessionOut | MfaRequiredOut:
    if user.totp_enabled_at is not None:
        token = issue_purpose_token(
            f"{user.id}:{method}", "mfa", MFA_TTL_SECONDS, settings=settings
        )
        return MfaRequiredOut(mfa_token=token)
    try:
        issued = await service.issue_session(
            session,
            settings,
            user,
            user_agent=request.headers.get("user-agent"),
            ip_hash=hash_ip(client_ip(request), settings),
            method=method,
        )
    except PermissionError as exc:
        raise unauthorized("This account is disabled.") from exc
    _set_refresh_cookie(response, settings, issued.refresh_token)
    return _session_out(issued, settings)


# --- Magic link ----------------------------------------------------------------------


@router.post(
    "/magic-link",
    response_model=AcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(limit("magic_link", 5, 60))],
)
async def request_magic_link(
    body: MagicLinkIn,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
) -> AcceptedOut:
    """Always 202, whether or not the email has an account (no enumeration)."""
    token = await service.create_magic_link(
        session, settings, body.email, hash_ip(client_ip(request), settings)
    )
    link = f"{settings.web_base_url.rstrip('/')}/auth/verify?token={token}"
    await send_email(
        registry,
        session,
        EmailMessage(
            to=body.email,
            subject="Your momentsales.ai sign-in link",
            text=(
                f"Sign in to momentsales.ai: {link}\n\n"
                f"This link works once and expires in {settings.magic_link_ttl_minutes} minutes."
                "\nIf you didn't ask for it, you can ignore this email.\n"
            ),
            tags={"category": "auth"},
        ),
    )
    return AcceptedOut()


@router.post(
    "/magic-link/verify",
    response_model=SessionOut | MfaRequiredOut,
    dependencies=[Depends(limit("magic_verify", 20, 60))],
)
async def verify_magic_link(
    body: TokenIn,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> SessionOut | MfaRequiredOut:
    user = await service.consume_magic_link(session, body.token)
    if user is None:
        raise ProblemError(
            400, "invalid_link", "Link not valid", "This link is invalid, used or expired."
        )
    return await _finish_sign_in(request, response, session, settings, user, "magic_link")


# --- 2FA challenge at sign-in --------------------------------------------------------


@router.post(
    "/2fa/challenge",
    response_model=SessionOut,
    dependencies=[Depends(limit("mfa_challenge", 10, 60))],
)
async def mfa_challenge(
    body: MfaChallengeIn,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
) -> SessionOut:
    try:
        claims = decode_token(body.mfa_token, "mfa", settings)
        user_id_raw, method = str(claims["sub"]).split(":", 1)
        user_id = uuid.UUID(user_id_raw)
    except (jwt.PyJWTError, ValueError) as exc:
        raise unauthorized("Your sign-in expired. Start again.") from exc
    user = await session.get(User, user_id)
    secret = (
        decrypt_secret(user.totp_secret_enc, settings) if user and user.totp_secret_enc else None
    )
    if user is None or secret is None or not verify_totp(secret, body.code):
        raise ProblemError(401, "invalid_code", "Wrong code", "That code didn't match.")
    issued = await service.issue_session(
        session,
        settings,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_hash=hash_ip(client_ip(request), settings),
        method=f"{method}+totp",
    )
    _set_refresh_cookie(response, settings, issued.refresh_token)
    return _session_out(issued, settings)


# --- Refresh / logout ----------------------------------------------------------------


@router.post(
    "/refresh",
    response_model=SessionOut,
    dependencies=[Depends(require_csrf_header), Depends(limit("refresh", 60, 60))],
)
async def refresh(
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    ms_refresh: Annotated[str | None, Cookie()] = None,
) -> SessionOut | JSONResponse:
    if not ms_refresh:
        raise unauthorized()
    outcome, issued = await service.rotate_refresh(
        session,
        settings,
        ms_refresh,
        user_agent=request.headers.get("user-agent"),
        ip_hash=hash_ip(client_ip(request), settings),
    )
    if issued is None:
        # Return (not raise) so a reuse-triggered family revocation is committed.
        body = {
            "type": "unauthorized",
            "title": "Unauthorized",
            "status": 401,
            "detail": "Your session has ended. Sign in again.",
            "reason": outcome,
        }
        resp = JSONResponse(body, status_code=401, media_type=PROBLEM_JSON)
        _clear_refresh_cookie(resp, settings)
        return resp
    _set_refresh_cookie(response, settings, issued.refresh_token)
    return _session_out(issued, settings)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf_header)],
)
async def logout(
    session: DbSession,
    settings: AppSettings,
    ms_refresh: Annotated[str | None, Cookie()] = None,
) -> Response:
    if ms_refresh:
        row = await session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(ms_refresh))
        )
        if row is not None:
            await service.revoke_family(session, row.family_id)
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(resp, settings)
    return resp


# --- Google OIDC ---------------------------------------------------------------------


@router.get("/google/start", dependencies=[Depends(limit("google_start", 20, 60))])
async def google_start(session: DbSession, settings: AppSettings, registry: Registry) -> Response:
    try:
        idp = await registry.identity(session)
    except ProviderConfigError as exc:
        raise ProblemError(503, "identity_unavailable", "Google sign-in unavailable") from exc
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    url = await idp.authorization_url(
        state=state, nonce=nonce, redirect_uri=settings.google_redirect_uri
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        OAUTH_COOKIE,
        sign({"k": "oauth", "state": state, "nonce": nonce}, ttl_seconds=600, settings=settings),
        max_age=600,
        path=OAUTH_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",  # must survive the top-level redirect back from Google
    )
    return resp


@router.get("/google/callback")
async def google_callback(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    registry: Registry,
    code: str | None = None,
    state: str | None = None,
    ms_oauth: Annotated[str | None, Cookie()] = None,
) -> Response:
    web = settings.web_base_url.rstrip("/")
    fail = RedirectResponse(f"{web}/sign-in?error=google", status_code=302)
    fail.delete_cookie(OAUTH_COOKIE, path=OAUTH_PATH)
    stored = unsign(ms_oauth, settings) if ms_oauth else None
    if not code or not state or not stored or stored.get("state") != state:
        return fail
    try:
        idp = await registry.identity(session)
        identity = await idp.exchange(
            code=code, redirect_uri=settings.google_redirect_uri, nonce=str(stored["nonce"])
        )
    except (IdentityError, ProviderConfigError):
        return fail
    user = await service.user_from_identity(session, identity)
    if user is None:
        return fail
    if user.totp_enabled_at is not None:
        mfa = issue_purpose_token(f"{user.id}:google", "mfa", MFA_TTL_SECONDS, settings=settings)
        resp: Response = RedirectResponse(
            f"{web}/auth/verify?{urlencode({'mfa_token': mfa})}", status_code=302
        )
    else:
        try:
            issued = await service.issue_session(
                session,
                settings,
                user,
                user_agent=request.headers.get("user-agent"),
                ip_hash=hash_ip(client_ip(request), settings),
                method="google",
            )
        except PermissionError:
            return fail
        resp = RedirectResponse(f"{web}/auth/callback", status_code=302)
        _set_refresh_cookie(resp, settings, issued.refresh_token)
    resp.delete_cookie(OAUTH_COOKIE, path=OAUTH_PATH)
    return resp


# --- Me + TOTP enrolment -------------------------------------------------------------


@router.get("/me", response_model=MeOut)
async def me(principal: CurrentPrincipal, session: DbSession) -> MeOut:
    rows = (
        await session.execute(
            select(Membership, Workspace)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(
                Membership.user_id == principal.user_id,
                Membership.status == MembershipStatus.ACTIVE,
            )
            .order_by(Workspace.name)
        )
    ).all()
    return MeOut(
        user=user_out(principal.user),
        memberships=[
            MembershipOut(
                workspace_id=w.id, workspace_name=w.name, workspace_slug=w.slug, role=m.role.value
            )
            for m, w in rows
        ],
    )


@router.post("/2fa/enroll", response_model=TotpEnrollOut)
async def totp_enroll(principal: CurrentPrincipal, settings: AppSettings) -> TotpEnrollOut:
    user = principal.user
    if user.totp_enabled_at is not None:
        raise ProblemError(409, "totp_already_enabled", "2FA is already on")
    secret = new_totp_secret()
    user.totp_secret_enc = encrypt_secret(secret, settings)
    return TotpEnrollOut(secret=secret, otpauth_uri=totp_uri(secret, user.email))


@router.post(
    "/2fa/verify",
    response_model=UserOut,
    dependencies=[Depends(limit("totp_verify", 10, 60))],
)
async def totp_verify(
    body: TotpCodeIn, principal: CurrentPrincipal, session: DbSession, settings: AppSettings
) -> UserOut:
    user = principal.user
    secret = decrypt_secret(user.totp_secret_enc, settings) if user.totp_secret_enc else None
    if secret is None:
        raise ProblemError(409, "totp_not_enrolled", "Start 2FA setup first")
    if not verify_totp(secret, body.code):
        raise ProblemError(400, "invalid_code", "Wrong code", "That code didn't match.")
    if user.totp_enabled_at is None:
        user.totp_enabled_at = datetime.now(UTC)
        await audit.record(
            session,
            action="auth.totp_enabled",
            object_type="user",
            object_id=user.id,
            workspace_id=None,
            actor_user_id=user.id,
            ip_hash=hash_ip(principal.ip, settings),
        )
    return user_out(user)


@router.post(
    "/2fa/disable",
    response_model=UserOut,
    dependencies=[Depends(limit("totp_disable", 10, 60))],
)
async def totp_disable(
    body: TotpCodeIn, principal: CurrentPrincipal, session: DbSession, settings: AppSettings
) -> UserOut:
    user = principal.user
    secret = decrypt_secret(user.totp_secret_enc, settings) if user.totp_secret_enc else None
    if user.totp_enabled_at is None or secret is None:
        raise ProblemError(409, "totp_not_enabled", "2FA is not on")
    if not verify_totp(secret, body.code):
        raise ProblemError(400, "invalid_code", "Wrong code", "That code didn't match.")
    user.totp_enabled_at = None
    user.totp_secret_enc = None
    await audit.record(
        session,
        action="auth.totp_disabled",
        object_type="user",
        object_id=user.id,
        workspace_id=None,
        actor_user_id=user.id,
        ip_hash=hash_ip(principal.ip, settings),
    )
    return user_out(user)
