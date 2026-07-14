"""验证已启动 API 的认证与最小 RAG 链路。

启动服务时请使用 Fake Provider，避免下载模型和产生模型调用费用。
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping
from typing import Any

import httpx


class SmokeTestError(RuntimeError):
    """冒烟测试的业务断言失败。"""


def _safe_http_error(response: httpx.Response, step: str) -> str:
    """生成不包含请求体、凭据或令牌的 HTTP 失败信息。"""
    code = "UNKNOWN_ERROR"
    request_id = response.headers.get("x-request-id", "未知")
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, Mapping) else {}
        if isinstance(error, Mapping):
            error_code = error.get("code")
            error_request_id = error.get("request_id")
            if isinstance(error_code, str) and error_code:
                code = error_code
            if isinstance(error_request_id, str) and error_request_id:
                request_id = error_request_id
    except (TypeError, ValueError):
        pass
    return f"{step}失败：HTTP {response.status_code}，错误码 {code}，request ID {request_id}。"


def _raise_for_status(response: httpx.Response, step: str) -> None:
    """用脱敏后的结构化信息报告 HTTP 失败。"""
    if not response.is_success:
        raise SmokeTestError(_safe_http_error(response, step))


async def _best_effort_logout(
    client: httpx.AsyncClient,
    base_url: str,
    origin: str,
) -> None:
    """失败清理不遮蔽原错误，也不输出响应正文或敏感请求信息。"""
    try:
        response = await client.post(
            f"{base_url}/api/v1/auth/logout",
            headers={"Origin": origin},
        )
        if response.is_success:
            return
        warning = f"退出清理警告：{_safe_http_error(response, '退出')}"
    except Exception as error:
        warning = f"退出清理警告：清理异常 {type(error).__name__}。"
    print(warning, file=sys.stderr)


async def wait_for_document_ready(
    client: httpx.AsyncClient,
    base_url: str,
    document_id: str,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    """轮询文档状态，直到处理成功或明确失败。"""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        remaining_seconds = deadline - asyncio.get_running_loop().time()
        if remaining_seconds <= 0:
            raise SmokeTestError("等待文档处理超时。")
        try:
            async with asyncio.timeout(remaining_seconds):
                response = await client.get(f"{base_url}/api/v1/documents/{document_id}")
        except TimeoutError:
            raise SmokeTestError("等待文档处理超时。") from None
        _raise_for_status(response, "查询文档状态")
        payload = response.json()
        status = payload["status"]
        if status == "ready":
            return payload
        if status == "failed":
            message = payload.get("error_message") or "文档处理失败。"
            raise SmokeTestError(message)
        remaining_seconds = deadline - asyncio.get_running_loop().time()
        if remaining_seconds <= 0:
            raise SmokeTestError(f"等待文档处理超时，最后状态为 {status}。")
        await asyncio.sleep(min(poll_interval_seconds, remaining_seconds))


async def run_smoke_test(
    base_url: str,
    timeout_seconds: float,
    *,
    environ: Mapping[str, str] = os.environ,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """登录后创建知识库、上传文本、等待入库，再验证回答和引用。"""
    username = environ.get("SMOKE_USERNAME")
    password = environ.get("SMOKE_PASSWORD")
    if not username or not password:
        raise SmokeTestError("必须通过 SMOKE_USERNAME 和 SMOKE_PASSWORD 提供冒烟测试账号。")
    origin = environ.get("SMOKE_ORIGIN", "http://127.0.0.1:5173")
    policy_text = "员工入职满一年后享有 5 天带薪年假。"
    async with httpx.AsyncClient(timeout=30.0, transport=transport) as client:
        health_response = await client.get(f"{base_url}/health")
        _raise_for_status(health_response, "健康检查")
        print("[1/9] 健康检查通过。")

        ready_response = await client.get(f"{base_url}/ready")
        _raise_for_status(ready_response, "就绪检查")
        print("[2/9] 就绪检查通过。")

        login_response = await client.post(
            f"{base_url}/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        _raise_for_status(login_response, "登录")
        access_token = login_response.json().get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise SmokeTestError("登录响应缺少 Access Token。")
        client.headers["Authorization"] = f"Bearer {access_token}"
        print("[3/9] 登录通过，认证信息已保存在当前进程内存中。")

        try:
            me_response = await client.get(f"{base_url}/api/v1/auth/me")
            _raise_for_status(me_response, "当前用户检查")
            if me_response.json().get("username") != username.strip().lower():
                raise SmokeTestError("当前用户与 SMOKE_USERNAME 不一致。")
            print("[4/9] 当前用户检查通过。")

            knowledge_base_response = await client.post(
                f"{base_url}/api/v1/knowledge-bases",
                json={
                    "name": "阶段 2B 冒烟测试知识库",
                    "description": "可安全重复创建的临时数据",
                },
            )
            _raise_for_status(knowledge_base_response, "创建知识库")
            knowledge_base_id = knowledge_base_response.json()["id"]
            print("[5/9] 知识库创建通过。")

            upload_response = await client.post(
                f"{base_url}/api/v1/knowledge-bases/{knowledge_base_id}/documents",
                files={"file": ("annual-leave.txt", policy_text.encode("utf-8"), "text/plain")},
            )
            _raise_for_status(upload_response, "上传文档")
            document_id = upload_response.json()["document_id"]
            print("[6/9] 文档上传通过。")
            await wait_for_document_ready(
                client,
                base_url,
                document_id,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=1,
            )
            print("[7/9] 文档处理完成。")

            question_response = await client.post(
                f"{base_url}/api/v1/knowledge-bases/{knowledge_base_id}/questions",
                json={"question": policy_text, "top_k": 1},
            )
            _raise_for_status(question_response, "知识库问答")
            answer = question_response.json()
            if not answer["citations"]:
                raise SmokeTestError("问答响应未返回引用。")
            if answer["citations"][0]["file_name"] != "annual-leave.txt":
                raise SmokeTestError("引用未指向上传的测试文档。")
            print("[8/9] 问答与引用检查通过。")
        except BaseException:
            await _best_effort_logout(client, base_url, origin)
            raise

        logout_response = await client.post(
            f"{base_url}/api/v1/auth/logout",
            headers={"Origin": origin},
        )
        _raise_for_status(logout_response, "退出")
        print("[9/9] 退出通过。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证 AI 知识库助手的认证与最小 RAG 链路")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=float, default=60)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    try:
        asyncio.run(run_smoke_test(arguments.base_url.rstrip("/"), arguments.timeout_seconds))
    except (httpx.HTTPError, SmokeTestError) as error:
        raise SystemExit(f"冒烟测试失败：{error}") from error
    print("冒烟测试通过：登录、创建、上传、入库、问答、引用和退出验证均成功。")
