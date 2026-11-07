"""Token, signing and encryption helpers (ADR-0003).

- Access tokens: short-lived HS256 JWTs.
- Opaque tokens (magic links, refresh, invitations): 256-bit random, stored only as
  SHA-256 hashes. High-entropy tokens do not need a slow password hash.
- Signed values (OAuth state, unsubscribe links): HMAC-SHA256 with SIGNING_SECRET.
- TOTP secrets: Fernet-encrypted at rest.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp
from cryptography.fernet import Fernet, InvalidToken

from momentsales.core.config import Settings, get_settings

JWT_ALG = "HS256"
JWT_ISSUER = "momentsales.ai"


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- JWT -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: uuid.UUID
    session_family: uuid.UUID
    platform_admin: bool


def issue_access_token(
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    *,
    platform_admin: bool = False,
    settings: Settings | None = None,
) -> str:
    s = settings or get_settings()
    now = datetime.now(UTC)
    payload = {
        "iss": JWT_ISSUER,
        "sub": str(user_id),
        "sid": str(family_id),
        "pa": platform_admin,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(seconds=s.access_token_ttl_seconds),
    }
    return jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm=JWT_ALG)


def issue_purpose_token(
    subject: str, purpose: str, ttl_seconds: int, settings: Settings | None = None
) -> str:
    """Short-lived JWT for one step of a flow (e.g. the 2FA challenge)."""
    s = settings or get_settings()
    now = datetime.now(UTC)
    payload = {
        "iss": JWT_ISSUER,
        "sub": subject,
        "typ": purpose,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm=JWT_ALG)


def decode_token(token: str, purpose: str, settings: Settings | None = None) -> dict[str, Any]:
    """Decode and verify a JWT. Raises jwt.PyJWTError on any problem."""
    s = settings or get_settings()
    claims: dict[str, Any] = jwt.decode(
        token,
        s.jwt_secret.get_secret_value(),
        algorithms=[JWT_ALG],
        issuer=JWT_ISSUER,
        options={"require": ["exp", "iat", "sub", "typ"]},
    )
    if claims.get("typ") != purpose:
        raise jwt.InvalidTokenError("wrong token type")
    return claims


def decode_access_token(token: str, settings: Settings | None = None) -> AccessClaims:
    claims = decode_token(token, "access", settings)
    return AccessClaims(
        user_id=uuid.UUID(claims["sub"]),
        session_family=uuid.UUID(claims["sid"]),
        platform_admin=bool(claims.get("pa", False)),
    )


# --- HMAC signing --------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign(payload: dict[str, Any], *, ttl_seconds: int, settings: Settings | None = None) -> str:
    s = settings or get_settings()
    body = dict(payload, exp=int(time.time()) + ttl_seconds)
    raw = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(s.signing_secret.get_secret_value().encode(), raw.encode(), hashlib.sha256)
    return f"{raw}.{_b64(mac.digest())}"


def unsign(value: str, settings: Settings | None = None) -> dict[str, Any] | None:
    """Return the payload if the signature is valid and not expired, else None."""
    s = settings or get_settings()
    try:
        raw, mac = value.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(s.signing_secret.get_secret_value().encode(), raw.encode(), hashlib.sha256)
    if not hmac.compare_digest(_b64(expected.digest()), mac):
        return None
    try:
        body: dict[str, Any] = json.loads(_unb64(raw))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(body.get("exp", 0)) < int(time.time()):
        return None
    return body


def hash_ip(ip: str | None, settings: Settings | None = None) -> str | None:
    if not ip:
        return None
    s = settings or get_settings()
    key = s.signing_secret.get_secret_value().encode()
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()


# --- TOTP ----------------------------------------------------------------------------


def _fernet(settings: Settings | None = None) -> Fernet:
    s = settings or get_settings()
    return Fernet(s.totp_encryption_key.get_secret_value().encode())


def new_totp_secret() -> str:
    return pyotp.random_base32()


def encrypt_secret(secret: str, settings: Settings | None = None) -> str:
    return _fernet(settings).encrypt(secret.encode()).decode()


def decrypt_secret(token: str, settings: Settings | None = None) -> str | None:
    try:
        return _fernet(settings).decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def totp_uri(secret: str, email: str) -> str:
    uri: str = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="momentsales.ai")
    return uri


def verify_totp(secret: str, code: str) -> bool:
    if not code.isdigit() or len(code) != 6:
        return False
    ok: bool = pyotp.TOTP(secret).verify(code, valid_window=1)
    return ok
