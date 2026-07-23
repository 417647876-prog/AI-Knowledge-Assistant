from contextvars import ContextVar, Token
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class RequestContext:
    request_id: str = ""
    user_id: str = ""
    knowledge_base_id: str = ""
    document_id: str = ""
    job_id: str = ""


_request_context: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def get_request_id() -> str:
    return get_request_context().request_id


def set_request_id(request_id: str) -> Token[RequestContext | None]:
    return _request_context.set(replace(get_request_context(), request_id=request_id))


def reset_request_id(token: Token[RequestContext | None]) -> None:
    _request_context.reset(token)


def get_request_context() -> RequestContext:
    return _request_context.get() or RequestContext()


def set_request_context(
    *,
    request_id: str = "",
    user_id: str = "",
    knowledge_base_id: str = "",
    document_id: str = "",
    job_id: str = "",
) -> Token[RequestContext | None]:
    return _request_context.set(
        RequestContext(
            request_id=request_id,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            job_id=job_id,
        )
    )


def reset_request_context(token: Token[RequestContext | None]) -> None:
    _request_context.reset(token)
