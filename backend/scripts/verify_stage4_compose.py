"""阶段 4 完整 Compose 的安全、有限时验收入口。"""

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

import httpx

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "documents" / "01-年假制度.txt"
)


class VerificationError(RuntimeError):
    """验收失败，消息必须适合直接输出且不包含敏感值。"""


_SAFE_LABEL = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


def _safe_label(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and _SAFE_LABEL.fullmatch(value) else fallback


@dataclass(frozen=True)
class VerificationOptions:
    base_url: str
    username: str
    password: str
    request_timeout_seconds: float
    wait_timeout_seconds: float
    poll_interval_seconds: float

    @classmethod
    def from_inputs(
        cls,
        *,
        base_url: str,
        username: str,
        password: str,
        request_timeout_seconds: float,
        wait_timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> "VerificationOptions":
        if not username.strip() or not password:
            raise VerificationError("必须通过命令行或环境变量提供显式测试账号和密码。")
        if (
            min(
                request_timeout_seconds,
                wait_timeout_seconds,
                poll_interval_seconds,
            )
            <= 0
        ):
            raise VerificationError("请求超时、总等待超时和轮询间隔都必须大于 0。")
        return cls(
            base_url=base_url.rstrip("/"),
            username=username.strip(),
            password=password,
            request_timeout_seconds=request_timeout_seconds,
            wait_timeout_seconds=wait_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )


def _request_id(response: httpx.Response) -> str:
    request_id = response.headers.get("x-request-id")
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, Mapping) else {}
        if isinstance(error, Mapping) and isinstance(error.get("request_id"), str):
            request_id = error["request_id"]
    except (TypeError, ValueError):
        pass
    return _safe_label(request_id, "unknown")


def _require_success(response: httpx.Response, step: str) -> None:
    if response.is_success:
        return
    code = "UNKNOWN_ERROR"
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, Mapping) else {}
        if isinstance(error, Mapping) and isinstance(error.get("code"), str):
            code = _safe_label(error["code"], "UNKNOWN_ERROR")
    except (TypeError, ValueError):
        pass
    raise VerificationError(
        f"{step}失败：HTTP {response.status_code}，错误码 {code}，"
        f"request_id={_request_id(response)}。"
    )


def _required_string(payload: object, field: str, step: str) -> str:
    value = payload.get(field) if isinstance(payload, Mapping) else None
    if not isinstance(value, str) or not value:
        raise VerificationError(f"{step}失败：响应缺少 {field}。")
    return value


def _response_json(response: httpx.Response, step: str) -> object:
    try:
        return response.json()
    except (TypeError, ValueError):
        raise VerificationError(
            f"{step}失败：响应不是有效 JSON，request_id={_request_id(response)}。"
        ) from None


def _contains_id(items: object, expected_id: str) -> bool:
    return isinstance(items, list) and any(
        isinstance(item, Mapping) and item.get("id") == expected_id for item in items
    )


async def wait_for_document_ready(
    client: httpx.AsyncClient,
    options: VerificationOptions,
    document_id: str,
) -> httpx.Response:
    """在总截止时间内等待文档完成，单次请求也受剩余时间限制。"""
    deadline = monotonic() + options.wait_timeout_seconds
    last_status = "unknown"
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise VerificationError(f"等待文档处理超时，最后状态为 {last_status}。")
        try:
            async with asyncio.timeout(remaining):
                response = await client.get(f"{options.base_url}/api/v1/documents/{document_id}")
        except TimeoutError:
            raise VerificationError(f"等待文档处理超时，最后状态为 {last_status}。") from None
        _require_success(response, "查询文档状态")
        payload = _response_json(response, "查询文档状态")
        last_status = _safe_label(
            payload.get("status") if isinstance(payload, Mapping) else None,
            "unknown",
        )
        if last_status == "ready":
            return response
        if last_status == "failed":
            raise VerificationError("文档处理失败：服务返回 failed 状态。")
        await asyncio.sleep(min(options.poll_interval_seconds, max(0, deadline - monotonic())))


async def verify_sse(
    client: httpx.AsyncClient,
    options: VerificationOptions,
    conversation_id: str,
) -> str:
    """消费到 done 才算成功；不保留或输出 token 正文。"""
    event_name = ""
    data_lines: list[str] = []

    def finish_event() -> str | None:
        nonlocal event_name, data_lines
        name, raw_data = event_name, "\n".join(data_lines)
        event_name, data_lines = "", []
        if name not in {"done", "error"}:
            return None
        try:
            payload = json.loads(raw_data)
        except (TypeError, ValueError):
            raise VerificationError(f"SSE {name} 事件格式无效。") from None
        if not isinstance(payload, Mapping):
            raise VerificationError(f"SSE {name} 事件格式无效。")
        safe_request_id = _safe_label(payload.get("request_id"), "unknown")
        if name == "error":
            safe_code = _safe_label(payload.get("code"), "UNKNOWN_ERROR")
            raise VerificationError(f"SSE 失败：错误码 {safe_code}，request_id={safe_request_id}。")
        return safe_request_id

    try:
        async with asyncio.timeout(options.wait_timeout_seconds):
            async with client.stream(
                "POST",
                f"{options.base_url}/api/v1/conversations/{conversation_id}/messages/stream",
                json={"question": "员工入职满一年有几天带薪年假？", "top_k": 1},
            ) as response:
                _require_success(response, "SSE 问答")
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line:
                        done_request_id = finish_event()
                        if done_request_id is not None:
                            return done_request_id
    except TimeoutError:
        raise VerificationError("SSE 等待超时，未收到 done 事件。") from None
    raise VerificationError("SSE 流中断，未收到 done 事件。")


async def run_verification(
    options: VerificationOptions,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    output: Callable[[str], None] = print,
    fixture_path: Path = DEFAULT_FIXTURE,
) -> None:
    """执行验收；HTTP adapter 可替换，以便在同一接口上做有限时单测。"""
    started = monotonic()
    timeout = httpx.Timeout(options.request_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:

        async def request(step: str, method: str, path: str, **kwargs) -> httpx.Response:
            step_started = monotonic()
            response = await client.request(method, f"{options.base_url}{path}", **kwargs)
            _require_success(response, step)
            elapsed_ms = round((monotonic() - step_started) * 1000)
            output(f"[PASS] {step} request_id={_request_id(response)} elapsed_ms={elapsed_ms}")
            return response

        await request("健康检查", "GET", "/health")
        await request("就绪检查", "GET", "/api/ready")
        login = await request(
            "登录",
            "POST",
            "/api/v1/auth/login",
            json={"username": options.username, "password": options.password},
        )
        login_payload = _response_json(login, "登录")
        access_token = (
            login_payload.get("access_token") if isinstance(login_payload, Mapping) else None
        )
        if not isinstance(access_token, str) or not access_token:
            raise VerificationError("登录失败：响应缺少访问令牌。")
        client.headers["Authorization"] = f"Bearer {access_token}"

        knowledge_base = await request(
            "创建知识库",
            "POST",
            "/api/v1/knowledge-bases",
            json={"name": "阶段4容器验收", "description": "临时验收数据"},
        )
        knowledge_base_id = _required_string(
            _response_json(knowledge_base, "创建知识库"), "id", "创建知识库"
        )
        try:
            fixture_content = fixture_path.read_bytes()
        except OSError:
            raise VerificationError("上传文档失败：验收 fixture 不可读取。") from None
        upload = await request(
            "上传文档",
            "POST",
            f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
            files={"file": (fixture_path.name, fixture_content, "text/plain")},
        )
        document_id = _required_string(
            _response_json(upload, "上传文档"), "document_id", "上传文档"
        )
        step_started = monotonic()
        document = await wait_for_document_ready(client, options, document_id)
        output(
            f"[PASS] 文档处理 request_id={_request_id(document)} "
            f"elapsed_ms={round((monotonic() - step_started) * 1000)}"
        )

        conversation = await request(
            "创建会话",
            "POST",
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": "阶段4容器验收会话"},
        )
        conversation_id = _required_string(
            _response_json(conversation, "创建会话"), "id", "创建会话"
        )
        step_started = monotonic()
        stream_request_id = await verify_sse(client, options, conversation_id)
        output(
            f"[PASS] SSE问答 request_id={stream_request_id} "
            f"elapsed_ms={round((monotonic() - step_started) * 1000)}"
        )

        detail = await request(
            "会话持久化",
            "GET",
            f"/api/v1/conversations/{conversation_id}",
        )
        detail_payload = _response_json(detail, "会话持久化")
        messages = detail_payload.get("messages") if isinstance(detail_payload, Mapping) else None
        assistant_message_id = (
            next(
                (
                    item.get("id")
                    for item in reversed(messages)
                    if isinstance(item, Mapping)
                    and item.get("role") == "assistant"
                    and item.get("status") == "completed"
                    and isinstance(item.get("id"), str)
                ),
                None,
            )
            if isinstance(messages, list)
            else None
        )
        if not isinstance(assistant_message_id, str):
            raise VerificationError("会话持久化失败：未找到已完成的助手消息。")

        now = datetime.now(UTC)
        await request(
            "用量查询",
            "GET",
            "/api/v1/me/usage",
            params={
                "from": (now - timedelta(days=1)).isoformat(),
                "to": (now + timedelta(minutes=1)).isoformat(),
            },
        )
        feedback = await request(
            "提交反馈",
            "PUT",
            f"/api/v1/messages/{assistant_message_id}/feedback",
            json={"helpful": True, "reason": "helpful_cited"},
        )
        feedback_id = _required_string(_response_json(feedback, "提交反馈"), "id", "提交反馈")
        feedback_page = await request("反馈查询", "GET", "/api/v1/me/feedback")
        feedback_payload = _response_json(feedback_page, "反馈查询")
        feedback_items = (
            feedback_payload.get("items") if isinstance(feedback_payload, Mapping) else None
        )
        if not _contains_id(feedback_items, feedback_id):
            raise VerificationError("反馈查询失败：未找到刚提交的反馈。")

        await request("删除文档", "DELETE", f"/api/v1/documents/{document_id}")
        document_trash = await request("文档回收站查询", "GET", "/api/v1/trash")
        document_trash_payload = _response_json(document_trash, "文档回收站查询")
        trashed_documents = (
            document_trash_payload.get("documents")
            if isinstance(document_trash_payload, Mapping)
            else None
        )
        if not _contains_id(trashed_documents, document_id):
            raise VerificationError("文档回收站查询失败：删除的文档不可见。")
        await request(
            "恢复文档",
            "POST",
            f"/api/v1/documents/{document_id}/restore",
        )
        await request("恢复文档检查", "GET", f"/api/v1/documents/{document_id}")

        await request(
            "删除知识库",
            "DELETE",
            f"/api/v1/knowledge-bases/{knowledge_base_id}",
        )
        knowledge_base_trash = await request("知识库回收站查询", "GET", "/api/v1/trash")
        knowledge_base_trash_payload = _response_json(knowledge_base_trash, "知识库回收站查询")
        trashed_knowledge_bases = (
            knowledge_base_trash_payload.get("knowledge_bases")
            if isinstance(knowledge_base_trash_payload, Mapping)
            else None
        )
        if not _contains_id(trashed_knowledge_bases, knowledge_base_id):
            raise VerificationError("知识库回收站查询失败：删除的知识库不可见。")
        await request(
            "恢复知识库",
            "POST",
            f"/api/v1/knowledge-bases/{knowledge_base_id}/restore",
        )
        knowledge_bases = await request("恢复知识库检查", "GET", "/api/v1/knowledge-bases")
        if not _contains_id(_response_json(knowledge_bases, "恢复知识库检查"), knowledge_base_id):
            raise VerificationError("恢复知识库检查失败：知识库未恢复。")
    output(f"[PASS] 阶段4验收 total_ms={round((monotonic() - started) * 1000)}")


def _parse_args(argv: list[str] | None, environ: Mapping[str, str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证阶段 4 完整 Compose 业务闭环")
    parser.add_argument(
        "--base-url",
        default=environ.get("STAGE4_VERIFY_BASE_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument("--username", default=environ.get("STAGE4_VERIFY_USERNAME", ""))
    parser.add_argument(
        "--password",
        default=environ.get("STAGE4_VERIFY_PASSWORD", ""),
        help="测试密码；优先通过 STAGE4_VERIFY_PASSWORD 环境变量提供",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=environ.get("STAGE4_VERIFY_REQUEST_TIMEOUT_SECONDS", "15"),
    )
    parser.add_argument(
        "--wait-timeout-seconds",
        type=float,
        default=environ.get("STAGE4_VERIFY_WAIT_TIMEOUT_SECONDS", "180"),
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=environ.get("STAGE4_VERIFY_POLL_INTERVAL_SECONDS", "1"),
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path(environ.get("STAGE4_VERIFY_FIXTURE", str(DEFAULT_FIXTURE))),
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    arguments = _parse_args(argv, environ)
    try:
        options = VerificationOptions.from_inputs(
            base_url=arguments.base_url,
            username=arguments.username,
            password=arguments.password,
            request_timeout_seconds=arguments.request_timeout_seconds,
            wait_timeout_seconds=arguments.wait_timeout_seconds,
            poll_interval_seconds=arguments.poll_interval_seconds,
        )
        asyncio.run(
            run_verification(
                options,
                transport=transport,
                fixture_path=arguments.fixture,
            )
        )
    except VerificationError as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    except httpx.HTTPError as error:
        print(f"[FAIL] HTTP 请求异常：{type(error).__name__}。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
