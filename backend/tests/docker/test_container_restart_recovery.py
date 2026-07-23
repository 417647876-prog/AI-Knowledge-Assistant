"""真实 Compose 重启恢复验收；普通 pytest 导入本模块时整体跳过。"""

import asyncio
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import httpx
import pytest

from scripts.verify_stage4_compose import (
    VerificationOptions,
    run_verification,
    verify_sse,
)

if os.environ.get("RUN_DOCKER_TESTS") != "1":
    pytest.skip("设置 RUN_DOCKER_TESTS=1 后才执行真实容器重启验收", allow_module_level=True)

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = ROOT / "deploy" / "docker-compose.yml"


def _compose(*arguments: str, timeout_seconds: float = 90) -> None:
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), *arguments],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as error:
        action = " ".join(arguments[:2])
        raise AssertionError(
            f"Docker Compose 操作失败：{action}（{type(error).__name__}）。"
        ) from None


def _require_success(response: httpx.Response, step: str) -> None:
    assert response.is_success, f"{step}失败：HTTP {response.status_code}。"


async def _wait_ready(client: httpx.AsyncClient, base_url: str, timeout_seconds: float) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        try:
            response = await client.get(f"{base_url}/api/ready")
            if response.is_success:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1)
    raise AssertionError("Compose 就绪检查超时。")


def _postgres_scalar(query: str, timeout_seconds: float = 20) -> str:
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "knowledge",
                "-d",
                "knowledge",
                "-tA",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                query,
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        raise AssertionError("读取容器数据库验收证据失败。") from None
    return result.stdout.strip()


async def _wait_worker_heartbeat_after(
    restarted_after: datetime,
    timeout_seconds: float,
) -> None:
    deadline = monotonic() + timeout_seconds
    safe_timestamp = restarted_after.isoformat()
    while monotonic() < deadline:
        try:
            fresh = await asyncio.to_thread(
                _postgres_scalar,
                "SELECT CASE WHEN last_seen_at > "
                f"'{safe_timestamp}'::timestamptz THEN 1 ELSE 0 END "
                "FROM worker_heartbeats WHERE worker_id = 'knowledge-worker';",
            )
            if fresh == "1":
                return
        except AssertionError:
            pass
        await asyncio.sleep(1)
    raise AssertionError("PostgreSQL 重启后 Worker 未产生新的健康心跳。")


async def _audit_count(resource_id: str) -> int:
    safe_resource_id = str(UUID(resource_id))
    value = await asyncio.to_thread(
        _postgres_scalar,
        "SELECT count(*) FROM audit_events "
        f"WHERE resource_id = '{safe_resource_id}' AND action = 'document.delete';",
    )
    return int(value)


async def _wait_document(
    client: httpx.AsyncClient,
    base_url: str,
    document_id: str,
    timeout_seconds: float,
) -> None:
    deadline = monotonic() + timeout_seconds
    last_status = "unknown"
    while monotonic() < deadline:
        response = await client.get(f"{base_url}/api/v1/documents/{document_id}")
        _require_success(response, "查询恢复文档")
        last_status = response.json().get("status", "unknown")
        if last_status == "ready":
            return
        if last_status == "failed":
            raise AssertionError("Worker 重启后的文档处理失败。")
        await asyncio.sleep(1)
    raise AssertionError(f"Worker 重启恢复超时，最后状态为 {last_status}。")


async def _wait_job_processing(
    client: httpx.AsyncClient,
    base_url: str,
    job_id: str,
    timeout_seconds: float,
) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        response = await client.get(f"{base_url}/api/v1/admin/operations/jobs?limit=100")
        _require_success(response, "查询 Worker 任务")
        item = next(
            (value for value in response.json().get("items", []) if value.get("id") == job_id),
            None,
        )
        if item is not None and item.get("status") == "processing":
            return
        if item is not None and item.get("status") in {"succeeded", "failed", "canceled"}:
            raise AssertionError("未能在任务结束前观察到 processing，无法证明租约恢复。")
        await asyncio.sleep(0.1)
    raise AssertionError("等待 Worker 任务进入 processing 超时。")


@pytest.mark.asyncio
async def test_worker_and_service_restarts_preserve_business_state() -> None:
    username = os.environ.get("STAGE4_VERIFY_USERNAME", "")
    password = os.environ.get("STAGE4_VERIFY_PASSWORD", "")
    assert username and password, "必须通过环境变量提供阶段 4 测试账号和密码。"
    base_url = os.environ.get("STAGE4_VERIFY_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    recovery_timeout = float(os.environ.get("STAGE4_DOCKER_RECOVERY_TIMEOUT_SECONDS", "300"))
    options = VerificationOptions.from_inputs(
        base_url=base_url,
        username=username,
        password=password,
        request_timeout_seconds=15,
        wait_timeout_seconds=180,
        poll_interval_seconds=1,
    )
    await run_verification(options, output=lambda _message: None)

    async with httpx.AsyncClient(timeout=15) as client:
        login = await client.post(
            f"{base_url}/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        _require_success(login, "登录")
        login_payload = login.json()
        assert login_payload.get("user", {}).get("role") == "admin", "重启验收账号必须是管理员。"
        client.headers["Authorization"] = f"Bearer {login_payload['access_token']}"

        knowledge_bases = await client.get(f"{base_url}/api/v1/knowledge-bases")
        _require_success(knowledge_bases, "定位验收知识库")
        knowledge_base_id = next(
            item["id"]
            for item in reversed(knowledge_bases.json())
            if item.get("name") == "阶段4容器验收"
        )
        conversations = await client.get(
            f"{base_url}/api/v1/knowledge-bases/{knowledge_base_id}/conversations"
        )
        _require_success(conversations, "定位验收会话")
        conversation_id = conversations.json()["items"][-1]["id"]

        await asyncio.to_thread(_compose, "stop", "worker")
        recovery_document_id: str | None = None
        try:
            marker = f"阶段4恢复验收-{uuid4()}。".encode()
            content = marker * max(1, (8 * 1024 * 1024) // len(marker))
            upload = await client.post(
                f"{base_url}/api/v1/knowledge-bases/{knowledge_base_id}/documents",
                files={"file": (f"recovery-{uuid4()}.txt", content, "text/plain")},
            )
            _require_success(upload, "上传恢复验收文档")
            recovery_document_id = upload.json()["document_id"]
            job_id = upload.json()["job_id"]
        finally:
            await asyncio.to_thread(_compose, "start", "worker")

        assert recovery_document_id is not None
        await _wait_job_processing(client, base_url, job_id, 60)
        await asyncio.to_thread(_compose, "restart", "worker")
        await _wait_document(client, base_url, recovery_document_id, recovery_timeout)

        await client.delete(f"{base_url}/api/v1/documents/{recovery_document_id}")
        trash = await client.get(f"{base_url}/api/v1/trash")
        _require_success(trash, "创建持久化回收站证据")
        assert any(item["id"] == recovery_document_id for item in trash.json()["documents"])
        audit_count_before_restart = await _audit_count(recovery_document_id)
        assert audit_count_before_restart >= 1, "PostgreSQL 重启前缺少文档删除审计。"

        await asyncio.to_thread(_compose, "restart", "api")
        await _wait_ready(client, base_url, 90)
        await asyncio.to_thread(_compose, "restart", "postgres")
        postgres_restart_completed_at = datetime.now(UTC)
        await _wait_ready(client, base_url, 120)
        await _wait_worker_heartbeat_after(postgres_restart_completed_at, 120)
        await asyncio.to_thread(_compose, "restart", "gateway")
        await _wait_ready(client, base_url, 90)

        refresh = await client.post(
            f"{base_url}/api/v1/auth/refresh",
            headers={"Origin": base_url},
        )
        _require_success(refresh, "gateway 重启后的同源 Cookie 会话恢复")
        client.headers["Authorization"] = f"Bearer {refresh.json()['access_token']}"
        await verify_sse(client, options, conversation_id)

        now = datetime.now(UTC)
        usage = await client.get(
            f"{base_url}/api/v1/me/usage",
            params={
                "from": (now - timedelta(days=1)).isoformat(),
                "to": (now + timedelta(minutes=1)).isoformat(),
            },
        )
        feedback = await client.get(f"{base_url}/api/v1/me/feedback")
        trash = await client.get(f"{base_url}/api/v1/trash")
        assert usage.is_success and feedback.is_success and trash.is_success
        assert feedback.json()["total"] >= 1
        assert any(item["id"] == recovery_document_id for item in trash.json()["documents"])
        assert await _audit_count(recovery_document_id) >= audit_count_before_restart

        restore = await client.post(f"{base_url}/api/v1/documents/{recovery_document_id}/restore")
        _require_success(restore, "恢复重启验收文档")
