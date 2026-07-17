import json
import logging
import sys
from io import StringIO

from app.audit.service import add_audit_event
from app.core.logging import StructuredFormatter
from app.core.request_context import reset_request_context, set_request_context


def _record(extra: dict[str, object], exc_info=None) -> logging.LogRecord:
    record = logging.LogRecord(
        "api",
        logging.ERROR if exc_info else logging.INFO,
        __file__,
        1,
        "operation complete",
        (),
        exc_info,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_production_log_has_standard_context_and_redacts_sensitive_values() -> None:
    token = set_request_context(
        request_id="req-123",
        user_id="user-456",
        knowledge_base_id="kb-789",
        document_id="doc-000",
        job_id="job-111",
    )
    try:
        rendered = StructuredFormatter(production=True).format(
            _record(
                {
                    "route": "/api/v1/questions",
                    "status_code": 201,
                    "duration_ms": 12.5,
                    "password": "correct horse battery staple",
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "api_key": "api-secret",
                    "database_url": "postgresql://secret@db/knowledge",
                    "upload_path": "D:/uploads/private/report.pdf",
                    "question": "客户的私密问题",
                    "answer": "模型的私密回答",
                    "prompt": "私密提示词",
                }
            )
        )
    finally:
        reset_request_context(token)

    payload = json.loads(rendered)
    assert set(payload) == {
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
    }
    assert payload | {"timestamp": None} == {
        "timestamp": None,
        "level": "INFO",
        "service": "api",
        "message": "LOG_EVENT",
        "request_id": "req-123",
        "user_id": "user-456",
        "knowledge_base_id": "kb-789",
        "document_id": "doc-000",
        "job_id": "job-111",
        "route": "/api/v1/questions",
        "status_code": 201,
        "duration_ms": 12.5,
        "error_code": None,
    }
    for secret in (
        "correct horse battery staple",
        "access-secret",
        "refresh-secret",
        "api-secret",
        "postgresql://secret@db/knowledge",
        "D:/uploads/private/report.pdf",
        "客户的私密问题",
        "模型的私密回答",
        "私密提示词",
    ):
        assert secret not in rendered


def test_exception_log_only_keeps_type_safe_code_and_request_id() -> None:
    token = set_request_context(request_id="req-500")
    try:
        try:
            raise RuntimeError("third party response body: confidential")
        except RuntimeError:
            record = _record({"error_code": "UPSTREAM_UNAVAILABLE"}, exc_info=sys.exc_info())
            record.msg = "upstream error: confidential"
            rendered = StructuredFormatter(production=True).format(record)
    finally:
        reset_request_context(token)

    payload = json.loads(rendered)
    assert payload["request_id"] == "req-500"
    assert payload["error_code"] == "UPSTREAM_UNAVAILABLE"
    assert payload["exception_type"] == "RuntimeError"
    assert "confidential" not in rendered


def test_development_log_is_human_readable_and_context_is_reset() -> None:
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(StructuredFormatter(production=False))
    logger = logging.getLogger("structured-log-test")
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    token = set_request_context(request_id="req-local")
    try:
        logger.info("healthy")
    finally:
        reset_request_context(token)
    logger.info("after reset")

    lines = output.getvalue().splitlines()
    assert "request_id=req-local" in lines[0]
    assert "request_id=-" in lines[1]


def test_audit_summary_rejects_client_text_even_when_uppercase() -> None:
    captured = []

    class CapturingSession:
        def add(self, event: object) -> None:
            captured.append(event)

    add_audit_event(
        CapturingSession(),  # type: ignore[arg-type]
        actor_user_id=None,
        action="test.action",
        resource_type="test",
        resource_id=None,
        result="success",
        security_summary={"reason": "CLIENTSECRET", "size_bytes": 3},
    )

    assert captured[0].security_summary == {"size_bytes": 3}


def test_non_exception_message_and_args_never_emit_sensitive_values() -> None:
    formatter = StructuredFormatter(production=True)
    password = "correct horse battery staple"
    token = "access-token-secret"

    message_record = _record({})
    message_record.msg = f"database password={password} token={token}"
    args_record = _record({})
    args_record.msg = "upstream response: %s"
    args_record.args = (password,)

    for record in (message_record, args_record):
        rendered = formatter.format(record)
        assert json.loads(rendered)["message"] == "LOG_EVENT"
        assert password not in rendered
        assert token not in rendered
        assert password not in StructuredFormatter(production=False).format(record)


def test_server_event_code_keeps_fixed_log_event_usable() -> None:
    record = _record({"event_code": "DOCUMENT_UPLOAD_CLEANUP_FAILED"})
    record.msg = "untrusted text must not become a log message"

    payload = json.loads(StructuredFormatter(production=True).format(record))
    assert payload["message"] == "DOCUMENT_UPLOAD_CLEANUP_FAILED"


def test_standard_route_and_error_code_fields_cannot_carry_sensitive_values() -> None:
    upload_path = "D:/uploads/private/customer-contract.pdf"
    token = "access-token-secret"
    record = _record({"route": upload_path, "error_code": token})

    production = StructuredFormatter(production=True).format(record)
    development = StructuredFormatter(production=False).format(record)

    payload = json.loads(production)
    assert payload["route"] is None
    assert payload["error_code"] is None
    assert upload_path not in production
    assert token not in production
    assert upload_path not in development
    assert token not in development


def test_route_template_and_service_error_code_remain_available() -> None:
    record = _record(
        {
            "route": "/api/v1/knowledge-bases/{knowledge_base_id}/documents",
            "error_code": "UPLOAD_QUOTA_EXCEEDED",
        }
    )

    payload = json.loads(StructuredFormatter(production=True).format(record))
    assert payload["route"] == "/api/v1/knowledge-bases/{knowledge_base_id}/documents"
    assert payload["error_code"] == "UPLOAD_QUOTA_EXCEEDED"
