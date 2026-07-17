import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.core.request_context import get_request_context

_STANDARD_FIELDS = (
    "timestamp",
    "level",
    "service",
    "message",
    "request_id",
    "user_id",
    "knowledge_base_id",
    "document_id",
    "job_id",
    "route",
    "status_code",
    "duration_ms",
    "error_code",
)


class StructuredFormatter(logging.Formatter):
    """A single logging seam that only emits an allow-listed operational schema."""

    def __init__(self, *, production: bool) -> None:
        super().__init__()
        self.production = production

    def format(self, record: logging.LogRecord) -> str:
        context = get_request_context()
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": record.name,
            "message": "exception" if record.exc_info else record.getMessage(),
            "request_id": context.request_id or None,
            "user_id": context.user_id or None,
            "knowledge_base_id": context.knowledge_base_id or None,
            "document_id": context.document_id or None,
            "job_id": context.job_id or None,
            "route": _safe_value(record, "route"),
            "status_code": _safe_value(record, "status_code"),
            "duration_ms": _safe_value(record, "duration_ms"),
            "error_code": _safe_value(record, "error_code"),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        if self.production:
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return " ".join(f"{name}={_render_text(value)}" for name, value in payload.items())


def configure_logging(*, production: bool) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter(production=production))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def _safe_value(record: logging.LogRecord, field: str) -> Any:
    value = getattr(record, field, None)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value) if field in {"route", "error_code"} else None


def _render_text(value: Any) -> str:
    return "-" if value is None else str(value)
