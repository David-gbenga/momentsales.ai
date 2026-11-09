from __future__ import annotations

import time
import uuid

import jwt
import pyotp
import pytest

from momentsales.core.config import Env, ProviderMode, Settings
from momentsales.core.ids import uuid7
from momentsales.core.security import (
    decode_access_token,
    decode_token,
    decrypt_secret,
    encrypt_secret,
    hash_token,
    issue_access_token,
    issue_purpose_token,
    new_totp_secret,
    sign,
    unsign,
    verify_totp,
)


def test_uuid7_is_version_7_and_time_ordered() -> None:
    a = uuid7()
    time.sleep(0.002)
    b = uuid7()
    assert a.version == 7 and b.version == 7
    assert a.variant == uuid.RFC_4122
    assert a < b


def test_access_token_round_trip() -> None:
    uid, fam = uuid7(), uuid7()
    claims = decode_access_token(issue_access_token(uid, fam, platform_admin=True))
    assert claims.user_id == uid and claims.session_family == fam and claims.platform_admin


def test_purpose_token_cannot_be_used_as_access_token() -> None:
    token = issue_purpose_token(str(uuid7()), "mfa", 60)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, "access")


def test_tampered_jwt_rejected() -> None:
    token = issue_access_token(uuid7(), uuid7())
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(token[:-2] + ("AA" if token[-2:] != "AA" else "BB"))


def test_sign_unsign_and_tamper() -> None:
    signed = sign({"k": "x", "v": 1}, ttl_seconds=60)
    assert unsign(signed) == {"k": "x", "v": 1, "exp": unsign(signed)["exp"]}  # type: ignore[index]
    raw, mac = signed.split(".")
    assert unsign(raw + "." + mac[::-1]) is None
    assert unsign("garbage") is None


def test_signed_value_expires() -> None:
    assert unsign(sign({"k": "x"}, ttl_seconds=-1)) is None


def test_token_hash_is_stable_sha256() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert len(hash_token("abc")) == 64


def test_totp_encrypt_and_verify() -> None:
    secret = new_totp_secret()
    enc = encrypt_secret(secret)
    assert enc != secret
    assert decrypt_secret(enc) == secret
    assert decrypt_secret("not-a-token") is None
    assert verify_totp(secret, pyotp.TOTP(secret).now())
    assert not verify_totp(secret, "abcdef")
    assert not verify_totp(secret, "12345")


def test_non_local_settings_refuse_dev_secrets() -> None:
    with pytest.raises(ValueError, match="jwt_secret"):
        Settings(env=Env.STAGING, cookie_secure=True)


def test_non_local_settings_refuse_disabled_rate_limits() -> None:
    with pytest.raises(ValueError, match="RATE_LIMIT_ENABLED"):
        Settings(env=Env.STAGING, rate_limit_enabled=False)


def test_production_refuses_mock_providers() -> None:
    strong = "x" * 40
    with pytest.raises(ValueError, match="PROVIDER_MODE=mock"):
        Settings(
            env=Env.PRODUCTION,
            jwt_secret=strong,  # type: ignore[arg-type]
            signing_secret=strong,  # type: ignore[arg-type]
            totp_encryption_key="Z" * 43 + "=",  # type: ignore[arg-type]
            integration_encryption_key="Y" * 43 + "=",  # type: ignore[arg-type]
            database_url="postgresql+psycopg://app_rw:real@db/ms",
            cookie_secure=True,
            stripe_webhook_secret="whsec_real_platform",  # type: ignore[arg-type]
            stripe_connect_webhook_secret="whsec_real_connect",  # type: ignore[arg-type]
            storage_backend="s3",
            provider_mode=ProviderMode.MOCK,
        )


def test_dev_tools_only_local_and_mock() -> None:
    assert Settings(env=Env.LOCAL, provider_mode=ProviderMode.MOCK).dev_tools_enabled
    assert not Settings(env=Env.LOCAL, provider_mode=ProviderMode.LIVE).dev_tools_enabled


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stripe_secret_key", "sk_live_abc"),
        ("stripe_secret_key", "rk_live_abc"),
        ("stripe_publishable_key", "pk_live_abc"),
    ],
)
def test_stripe_live_keys_are_refused_everywhere(field: str, value: str) -> None:
    """CLAUDE.md: Stripe stays in test mode."""
    with pytest.raises(ValueError, match="live key"):
        Settings(env=Env.LOCAL, **{field: value})  # type: ignore[arg-type]


def test_stripe_test_mode_detection() -> None:
    assert Settings(env=Env.LOCAL, provider_mode=ProviderMode.MOCK).stripe_test_mode
    assert Settings(
        env=Env.LOCAL,
        provider_mode=ProviderMode.LIVE,
        stripe_secret_key="sk_test_x",  # type: ignore[arg-type]
    ).stripe_test_mode


def test_outside_local_needs_real_webhook_secrets_and_object_storage() -> None:
    strong = "x" * 40
    with pytest.raises(ValueError) as err:
        Settings(
            env=Env.STAGING,
            jwt_secret=strong,  # type: ignore[arg-type]
            signing_secret=strong,  # type: ignore[arg-type]
            totp_encryption_key="Z" * 43 + "=",  # type: ignore[arg-type]
            integration_encryption_key="Y" * 43 + "=",  # type: ignore[arg-type]
            database_url="postgresql+psycopg://app_rw:real@db/ms",
            cookie_secure=True,
        )
    assert "stripe_webhook_secret" in str(err.value)
    assert "STORAGE_BACKEND=local" in str(err.value)
