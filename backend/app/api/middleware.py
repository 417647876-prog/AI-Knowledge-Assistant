import re
from collections.abc import Awaitable, Callable
from ipaddress import ip_address, ip_network
from time import perf_counter
from uuid import uuid4

from fastapi import Request, Response
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.metrics import metrics_registry
from app.core.request_context import reset_request_id, set_request_id
from app.core.security import TokenValidationError, decode_access_token

_UPLOAD_PATH = re.compile(r"^/api/v1/knowledge-bases/[^/]+/documents$")


def is_trusted_gateway_request(
    *, client_host: str | None, gateway_secret: str | None, settings: Settings
) -> bool:
    if gateway_secret != settings.gateway_shared_secret or not settings.trusted_gateway_networks:
        return False
    try:
        direct_address = ip_address(client_host or "")
    except ValueError:
        return False
    return any(
        direct_address in ip_network(network, strict=False)
        for network in settings.trusted_gateway_networks
    )


def resolve_request_source(
    *,
    client_host: str | None,
    forwarded_for: str | None,
    gateway_secret: str | None,
    settings: Settings,
) -> str:
    """仅受信 gateway 且密钥匹配时采纳最左侧 X-Forwarded-For。"""
    direct_source = client_host or "unknown"
    if not forwarded_for or not is_trusted_gateway_request(
        client_host=client_host, gateway_secret=gateway_secret, settings=settings
    ):
        return direct_source
    candidate = forwarded_for.split(",", 1)[0].strip()
    try:
        return str(ip_address(candidate))
    except ValueError:
        return direct_source


class RequestSourceMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_source = resolve_request_source(
            client_host=request.client.host if request.client is not None else None,
            forwarded_for=request.headers.get("X-Forwarded-For"),
            gateway_secret=request.headers.get("X-Gateway-Secret"),
            settings=self.settings,
        )
        request.state.via_gateway = is_trusted_gateway_request(
            client_host=request.client.host if request.client is not None else None,
            gateway_secret=request.headers.get("X-Gateway-Secret"),
            settings=self.settings,
        )
        return await call_next(request)


class _RequestBodyTooLarge(Exception):
    pass


class _BodyLimitExceededAfterResponseStarted(RuntimeError):
    pass


class _LimitedReceive:
    def __init__(self, receive: Receive, limit: int) -> None:
        self._receive = receive
        self._limit = limit
        self._received = 0
        self.exceeded = False

    async def __call__(self) -> Message:
        message = await self._receive()
        if message["type"] == "http.request":
            self._received += len(message.get("body", b""))
            if self._received > self._limit:
                self.exceeded = True
                raise _RequestBodyTooLarge
        return message


class UploadGuardMiddleware:
    """在 FastAPI 解析 multipart 前鉴权并限制文档上传请求体。"""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._matches_upload(scope):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        auth_error = self._validate_bearer(headers.get("authorization"))
        if auth_error is not None:
            await self._send_error(scope, send, 401, *auth_error)
            return

        request_limit = (
            self.settings.max_upload_bytes + self.settings.upload_multipart_overhead_bytes
        )
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = request_limit + 1
            if declared_length < 0 or declared_length > request_limit:
                await self._send_too_large(scope, send)
                return

        limited_receive = _LimitedReceive(receive, request_limit)
        response_started = False
        replacement_sent = False
        suppress_downstream = False
        protocol_violation = False

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            nonlocal replacement_sent
            nonlocal suppress_downstream
            nonlocal protocol_violation
            if suppress_downstream:
                return
            if limited_receive.exceeded:
                if response_started:
                    # UploadFile 会在端点执行前完整解析；若这里已开始响应，说明下游违反了
                    # 此守卫依赖的协议不变量。抑制剩余消息并在调用结束后传播异常。
                    protocol_violation = True
                    suppress_downstream = True
                    return
                response_started = True
                replacement_sent = True
                suppress_downstream = True
                await self._send_too_large(scope, send)
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _RequestBodyTooLarge as exc:
            if replacement_sent:
                pass
            elif response_started:
                raise _BodyLimitExceededAfterResponseStarted(
                    "上传响应开始后请求体才超过限制"
                ) from exc
            else:
                response_started = True
                replacement_sent = True
                await self._send_too_large(scope, send)

        if protocol_violation:
            raise _BodyLimitExceededAfterResponseStarted("上传响应开始后请求体才超过限制")
        if limited_receive.exceeded and not replacement_sent:
            if response_started:
                raise _BodyLimitExceededAfterResponseStarted("上传响应开始后请求体才超过限制")
            await self._send_too_large(scope, send)

    @staticmethod
    def _matches_upload(scope: Scope) -> bool:
        return (
            scope["type"] == "http"
            and scope.get("method") == "POST"
            and _UPLOAD_PATH.fullmatch(scope.get("path", "")) is not None
        )

    def _validate_bearer(self, authorization: str | None) -> tuple[str, str] | None:
        if authorization is None:
            return "AUTHENTICATION_REQUIRED", "用户未登录或账号已停用。"
        scheme, separator, token = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not separator or not token or " " in token:
            return "AUTHENTICATION_REQUIRED", "用户未登录或账号已停用。"
        try:
            decode_access_token(token, self.settings)
        except TokenValidationError as exc:
            return exc.code, "访问令牌无效或已过期。"
        return None

    async def _send_too_large(self, scope: Scope, send: Send) -> None:
        await self._send_error(
            scope,
            send,
            413,
            "FILE_TOO_LARGE",
            "上传请求体超过允许大小。",
        )

    @staticmethod
    async def _send_error(
        scope: Scope,
        send: Send,
        status_code: int,
        code: str,
        message: str,
    ) -> None:
        request_id = scope.get("state", {}).get("request_id", "")
        response = JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": request_id,
                }
            },
        )

        async def unused_receive() -> Message:
            return {"type": "http.disconnect"}

        await response(scope, receive=unused_receive, send=send)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        token = set_request_id(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            reset_request_id(token)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = perf_counter()
        status_code = 500
        is_sse = False
        try:
            response = await call_next(request)
            status_code = response.status_code
            is_sse = response.headers.get("content-type", "").startswith("text/event-stream")
            return response
        finally:
            metrics_registry.record_api_request(
                status_code=status_code,
                duration_ms=(perf_counter() - started) * 1000,
                is_sse=is_sse,
            )
