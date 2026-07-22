from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
START_CMD = PROJECT_ROOT / "启动项目.cmd"
STOP_CMD = PROJECT_ROOT / "停止项目.cmd"
START_SCRIPT = PROJECT_ROOT / "scripts" / "start-project.ps1"
STOP_SCRIPT = PROJECT_ROOT / "scripts" / "stop-project.ps1"
README = PROJECT_ROOT / "README.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_cmd_launchers_delegate_to_powershell_and_preserve_exit_code() -> None:
    expectations = [
        (START_CMD, r"scripts\start-project.ps1"),
        (STOP_CMD, r"scripts\stop-project.ps1"),
    ]

    for path, target in expectations:
        content = _read(path)
        assert "powershell.exe -NoProfile -ExecutionPolicy Bypass" in content
        assert target in content
        assert 'set "EXIT_CODE=%ERRORLEVEL%"' in content
        assert 'if not "%EXIT_CODE%"=="0"' in content
        assert "pause" in content.lower()
        assert "exit /b %EXIT_CODE%" in content


def test_start_script_guards_prerequisites_and_waits_for_api_ready() -> None:
    content = _read(START_SCRIPT)

    for required in (
        "[switch]$Build",
        "ReadyTimeoutSeconds = 180",
        "FreePhysicalMemory",
        "$minimumFreeKiB = 2GB / 1KB",
        "deploy/.env",
        "docker desktop --help",
        "docker desktop start",
        "docker info",
        "--build",
        "/api/ready",
        "StatusCode -eq 200",
        "Start-Process",
    ):
        assert required in content
    assert "/health" not in content


def _run_start_script_harness(
    tmp_path: Path, *, free_memory_kib: int, compose_exit_code: int, ready_status: int
) -> tuple[str, list[str]]:
    """在临时项目中用 PowerShell 函数替身运行脚本，绝不调用真实 Docker。"""
    project_root = tmp_path / "project with spaces"
    scripts_dir = project_root / "scripts"
    deploy_dir = project_root / "deploy"
    scripts_dir.mkdir(parents=True)
    deploy_dir.mkdir()
    shutil.copy2(START_SCRIPT, scripts_dir / "start-project.ps1")
    (deploy_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (deploy_dir / ".env").write_text("TEST_ONLY=1\n", encoding="utf-8")

    harness = tmp_path / "launcher-harness.ps1"
    harness.write_text(
        f"""$ErrorActionPreference = 'Stop'
$events = [System.Collections.Generic.List[string]]::new()
$scriptPath = '{(scripts_dir / 'start-project.ps1').as_posix()}'

function Get-CimInstance {{
    [pscustomobject]@{{ FreePhysicalMemory = {free_memory_kib} }}
}}
function docker {{
    $commandLine = $args -join ' '
    $events.Add("docker $commandLine")
    if ($commandLine -eq 'info') {{ $global:LASTEXITCODE = 0; return }}
    if ($commandLine -match 'compose .* up -d') {{ $global:LASTEXITCODE = {compose_exit_code}; return }}
    $global:LASTEXITCODE = 0
}}
function Invoke-WebRequest {{
    $events.Add('ready-check')
    [pscustomobject]@{{ StatusCode = {ready_status} }}
}}
function Start-Process {{
    $events.Add('browser-open')
}}
function Start-Sleep {{
    $events.Add('sleep')
}}

try {{
    & $scriptPath -ReadyTimeoutSeconds 10
    'RESULT:success'
}}
catch {{
    "RESULT:error:$($_.Exception.Message)"
}}
$events | ForEach-Object {{ "EVENT:$($_)" }}
""",
        encoding="utf-8-sig",
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    result = next(line.removeprefix("RESULT:") for line in lines if line.startswith("RESULT:"))
    events = [line.removeprefix("EVENT:") for line in lines if line.startswith("EVENT:")]
    return result, events


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
def test_start_script_rejects_low_memory_before_calling_docker(tmp_path: Path) -> None:
    result, events = _run_start_script_harness(
        tmp_path, free_memory_kib=1, compose_exit_code=0, ready_status=200
    )

    assert "可用物理内存低于 2 GiB" in result
    assert not any(event.startswith("docker ") for event in events)


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
def test_start_script_preserves_compose_failure_and_does_not_open_browser(tmp_path: Path) -> None:
    result, events = _run_start_script_harness(
        tmp_path, free_memory_kib=3 * 1024 * 1024, compose_exit_code=37, ready_status=200
    )

    assert "Docker Compose 启动失败" in result
    assert "37" in result
    assert any("compose" in event and "up -d" in event for event in events)
    assert "browser-open" not in events


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
def test_start_script_opens_browser_only_after_ready_200(tmp_path: Path) -> None:
    result, events = _run_start_script_harness(
        tmp_path, free_memory_kib=3 * 1024 * 1024, compose_exit_code=0, ready_status=200
    )

    assert result == "success"
    compose_up_index = next(i for i, event in enumerate(events) if "compose" in event and "up -d" in event)
    ready_index = events.index("ready-check")
    browser_index = events.index("browser-open")
    assert compose_up_index < ready_index < browser_index


def test_start_script_does_not_read_or_print_credentials() -> None:
    content = _read(START_SCRIPT).lower()

    for forbidden in (
        "get-content",
        "jwt_secret_key",
        "gateway_shared_secret",
        "chat_api_key",
        "embedding_api_key",
        "initial_admin_password",
    ):
        assert forbidden not in content


@pytest.mark.skipif(os.name != "nt", reason="CMD 启动器仅在 Windows 上执行")
@pytest.mark.parametrize(
    ("source_cmd", "script_name", "exit_code"),
    [
        (START_CMD, "start-project.ps1", 0),
        (STOP_CMD, "stop-project.ps1", 23),
    ],
)
def test_cmd_launcher_executes_powershell_stub_and_preserves_exit_code(
    tmp_path: Path,
    source_cmd: Path,
    script_name: str,
    exit_code: int,
) -> None:
    launcher_root = tmp_path / "launcher directory with spaces"
    scripts_dir = launcher_root / "scripts"
    scripts_dir.mkdir(parents=True)
    launcher_cmd = launcher_root / "launcher.cmd"
    shutil.copy2(source_cmd, launcher_cmd)

    marker = scripts_dir / "powershell-stub-called.txt"
    script_path = scripts_dir / script_name
    script_path.write_text(
        "Set-Content -LiteralPath (Join-Path $PSScriptRoot 'powershell-stub-called.txt') "
        "-Value 'called' -Encoding ascii\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "launcher.cmd"],
        cwd=launcher_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )

    assert completed.returncode == exit_code
    assert marker.read_text(encoding="utf-8").strip() == "called"
