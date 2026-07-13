"""验证已启动 API 的最小 RAG 链路。

启动服务时请使用 Fake Provider，避免下载模型和产生模型调用费用。
"""

import argparse
import asyncio
from typing import Any

import httpx


class SmokeTestError(RuntimeError):
    """冒烟测试的业务断言失败。"""


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
        response = await client.get(f"{base_url}/api/v1/documents/{document_id}")
        response.raise_for_status()
        payload = response.json()
        status = payload["status"]
        if status == "ready":
            return payload
        if status == "failed":
            message = payload.get("error_message") or "文档处理失败。"
            raise SmokeTestError(message)
        if asyncio.get_running_loop().time() >= deadline:
            raise SmokeTestError(f"等待文档处理超时，最后状态为 {status}。")
        await asyncio.sleep(poll_interval_seconds)


async def run_smoke_test(base_url: str, timeout_seconds: float) -> None:
    """创建知识库、上传文本、等待入库，再验证回答和引用。"""
    policy_text = "员工入职满一年后享有 5 天带薪年假。"
    async with httpx.AsyncClient(timeout=30.0) as client:
        knowledge_base_response = await client.post(
            f"{base_url}/api/v1/knowledge-bases",
            json={"name": "阶段 1E 冒烟测试知识库", "description": "可安全重复创建的临时数据"},
        )
        knowledge_base_response.raise_for_status()
        knowledge_base_id = knowledge_base_response.json()["id"]

        upload_response = await client.post(
            f"{base_url}/api/v1/knowledge-bases/{knowledge_base_id}/documents",
            files={"file": ("annual-leave.txt", policy_text.encode("utf-8"), "text/plain")},
        )
        upload_response.raise_for_status()
        document_id = upload_response.json()["document_id"]
        await wait_for_document_ready(
            client,
            base_url,
            document_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=1,
        )

        question_response = await client.post(
            f"{base_url}/api/v1/knowledge-bases/{knowledge_base_id}/questions",
            json={"question": policy_text, "top_k": 1},
        )
        question_response.raise_for_status()
        answer = question_response.json()
        if not answer["citations"]:
            raise SmokeTestError("问答响应未返回引用。")
        if answer["citations"][0]["file_name"] != "annual-leave.txt":
            raise SmokeTestError("引用未指向上传的测试文档。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证 AI 知识库助手的最小 RAG 链路")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=float, default=60)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    try:
        asyncio.run(run_smoke_test(arguments.base_url.rstrip("/"), arguments.timeout_seconds))
    except (httpx.HTTPError, SmokeTestError) as error:
        raise SystemExit(f"冒烟测试失败：{error}") from error
    print("冒烟测试通过：创建、上传、入库、问答和引用验证均成功。")
