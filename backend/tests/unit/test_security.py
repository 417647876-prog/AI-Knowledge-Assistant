from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.security import (
    TokenValidationError,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    hash_refresh_secret,
    verify_password,
)


def test_password_is_argon2_and_verifies() -> None:
    encoded = hash_password("correct horse battery")
    assert encoded.startswith("$argon2")
    assert verify_password("correct horse battery", encoded) is True
    assert verify_password("wrong password", encoded) is False


def test_access_token_contains_fixed_issuer_audience_and_expiry() -> None:
    now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    settings = Settings(_env_file=None, jwt_secret_key="x" * 64)
    user_id = uuid4()
    token = create_access_token(user_id=user_id, role="admin", settings=settings, now=now)
    claims = decode_access_token(token, settings, now=now + timedelta(minutes=1))
    assert claims.user_id == user_id
    assert claims.role == "admin"
    assert claims.expires_at == now + timedelta(minutes=15)


def test_expired_access_token_is_rejected() -> None:
    now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    settings = Settings(_env_file=None, jwt_secret_key="x" * 64)
    token = create_access_token(user_id=uuid4(), role="user", settings=settings, now=now)
    with pytest.raises(TokenValidationError, match="TOKEN_EXPIRED"):
        decode_access_token(token, settings, now=now + timedelta(minutes=16))


def test_refresh_token_only_exposes_hashable_random_secret() -> None:
    token = create_refresh_token()
    assert token.raw == f"{token.session_id}.{token.secret}"
    assert len(hash_refresh_secret(token.secret)) == 64
    assert token.secret not in hash_refresh_secret(token.secret)
