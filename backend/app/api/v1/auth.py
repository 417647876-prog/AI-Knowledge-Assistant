from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import get_current_user
from app.auth.schemas import AuthSessionResponse, CurrentUserResponse, LoginRequest
from app.auth.service import AuthService, IssuedSession
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthService:
    return AuthService(session=session, settings=settings)


@router.post("/login", response_model=AuthSessionResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthSessionResponse:
    issued = await service.login(payload.username, payload.password)
    _set_refresh_cookie(response, issued, settings)
    return _to_auth_response(issued)


@router.post("/refresh", response_model=AuthSessionResponse)
async def refresh(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)] = None,
) -> AuthSessionResponse:
    _require_trusted_origin(request, settings)
    if refresh_token is None:
        raise AppError(
            code="TOKEN_INVALID",
            message="刷新令牌无效。",
            status_code=401,
        )
    issued = await service.refresh(refresh_token)
    _set_refresh_cookie(response, issued, settings)
    return _to_auth_response(issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)] = None,
) -> Response:
    _require_trusted_origin(request, settings)
    await service.logout(refresh_token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/me", response_model=CurrentUserResponse)
async def me(
    user: Annotated[User, Depends(get_current_user)],
) -> CurrentUserResponse:
    return CurrentUserResponse.model_validate(user)


def _require_trusted_origin(request: Request, settings: Settings) -> None:
    if request.headers.get("origin") not in settings.trusted_origins:
        raise AppError(
            code="INVALID_ORIGIN",
            message="请求来源不受信任。",
            status_code=403,
        )


def _set_refresh_cookie(response: Response, issued: IssuedSession, settings: Settings) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=issued.refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path=REFRESH_COOKIE_PATH,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _to_auth_response(issued: IssuedSession) -> AuthSessionResponse:
    return AuthSessionResponse(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        user=CurrentUserResponse.model_validate(issued.user),
    )
