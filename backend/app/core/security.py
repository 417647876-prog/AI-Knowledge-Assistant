import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import Settings

_password_hash = PasswordHash.recommended()


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: UUID
    role: str
    token_id: UUID
    expires_at: datetime


@dataclass(frozen=True)
class RefreshTokenParts:
    session_id: UUID
    secret: str
    raw: str


class TokenValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


def create_access_token(
    *,
    user_id: UUID,
    role: str,
    settings: Settings,
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "role": role,
        "jti": str(uuid4()),
        "iat": issued_at,
        "exp": expires_at,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(
    token: str,
    settings: Settings,
    now: datetime | None = None,
) -> AccessTokenClaims:
    current_time = now or datetime.now(UTC)
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require": ["sub", "role", "jti", "iat", "exp"],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
        if (
            not isinstance(payload["iat"], int)
            or isinstance(payload["iat"], bool)
            or not isinstance(payload["exp"], int)
            or isinstance(payload["exp"], bool)
        ):
            raise TokenValidationError("TOKEN_INVALID")
        issued_at = datetime.fromtimestamp(payload["iat"], tz=UTC)
        expires_at = datetime.fromtimestamp(payload["exp"], tz=UTC)
        if issued_at > current_time or issued_at >= expires_at:
            raise TokenValidationError("TOKEN_INVALID")
        if current_time >= expires_at:
            raise TokenValidationError("TOKEN_EXPIRED")
        return AccessTokenClaims(
            user_id=UUID(payload["sub"]),
            role=payload["role"],
            token_id=UUID(payload["jti"]),
            expires_at=expires_at,
        )
    except TokenValidationError:
        raise
    except (InvalidTokenError, KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        raise TokenValidationError("TOKEN_INVALID") from exc


def create_refresh_token() -> RefreshTokenParts:
    session_id = uuid4()
    secret = secrets.token_urlsafe(32)
    return RefreshTokenParts(
        session_id=session_id,
        secret=secret,
        raw=f"{session_id}.{secret}",
    )


def hash_refresh_secret(secret: str) -> str:
    return sha256(secret.encode("utf-8")).hexdigest()
