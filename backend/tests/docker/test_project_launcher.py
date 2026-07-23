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
LAUNCHER_DESIGN = PROJECT_ROOT / "docs" / "设计" / "2026-07-23-项目一键启动设计.md"
LAUNCHER_PLAN = PROJECT_ROOT / "docs" / "superpowers" / "plans" / "2026-07-23-simple-project-launcher.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_deploy_env_is_ignored_but_example_remains_trackable() -> None:
    ignore_entries = _read(PROJECT_ROOT / ".gitignore").splitlines()

    assert ignore_entries.count("deploy/.env") == 1
    assert "deploy/.env.example" not in ignore_entries


def test_readme_documents_simple_project_launchers() -> None:
    content = _read(README)

    for required in (
        "## 一键启动",
        "启动项目.cmd",
        r".\scripts\start-project.ps1",
        r".\scripts\start-project.ps1 -Build",
        r".\scripts\start-project.ps1 -ProjectName stage5launcher",
        "停止项目.cmd",
        "Copy-Item deploy/.env.example deploy/.env",
        "ai-knowledge-assistant",
    ):
        assert required in content

    for required in (
        "手工创建并填写本地配置",
        "Copy-Item deploy/.env.example deploy/.env",
        "启动器不会自动生成 `deploy/.env`",
        "不会创建、修改或重置管理员凭据",
        "唯一判断",
        "/api/ready",
        "HTTP 200",
        "http://127.0.0.1:8080",
        "保留",
        "数据卷",
        "低于 2 GiB",
        "Docker Desktop",
        "docker desktop start",
        "8080 端口",
        "`/api/ready` 超时",
        "不代表已经配置远程访问",
        "远程访问",
        "另行配置",
    ):
        assert required in content


def test_readme_admin_command_uses_the_launcher_compose_owner() -> None:
    content = _read(README)
    launcher_section = content.split("## 一键启动", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]

    assert (
        "docker compose -p ai-knowledge-assistant -f deploy/docker-compose.yml "
        "exec api python -m scripts.create_admin --username \"YOUR_ADMIN_USERNAME\""
    ) in launcher_section


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
            "'desktop', '--help'",
            "'desktop', 'start'",
            "@('info')",
        "--build",
        "/api/ready",
        "StatusCode -eq 200",
        "Start-Process",
    ):
        assert required in content
    assert "/health" not in content


def test_launcher_docs_document_explicit_project_owner_isolation() -> None:
    for path in (LAUNCHER_DESIGN, LAUNCHER_PLAN):
        content = _read(path)
        assert "-ProjectName stage5launcher" in content
    assert "$env:COMPOSE_PROJECT_NAME" not in _read(LAUNCHER_PLAN)


def _run_start_script_harness(
    tmp_path: Path,
    *,
    free_memory_kib: int,
    compose_exit_code: int,
    ready_status: int,
    ready_statuses: tuple[int, ...] | None = None,
    info_failures_before_ready: int = 0,
    compose_writes_error: bool = False,
    build: bool = False,
    project_name: str | None = None,
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
$infoCalls = 0
$readyStatuses = @({', '.join(map(str, ready_statuses or (ready_status,)))})
$readyCall = 0
$scriptPath = '{(scripts_dir / 'start-project.ps1').as_posix()}'

function Get-CimInstance {{
    [pscustomobject]@{{ FreePhysicalMemory = {free_memory_kib} }}
}}
function docker {{
    $commandLine = $args -join ' '
    $events.Add("docker $commandLine")
    if ($commandLine -eq 'info') {{
        $script:infoCalls++
        if ($script:infoCalls -le {info_failures_before_ready}) {{
            Write-Error 'Docker daemon is unavailable (controlled stderr).'
            $global:LASTEXITCODE = 19
            return
        }}
        $global:LASTEXITCODE = 0
        return
    }}
    if ($commandLine -eq 'desktop --help') {{
        Write-Output '  start'
        $global:LASTEXITCODE = 0
        return
    }}
    if ($commandLine -match 'compose .* up -d') {{
        if ({'$true' if compose_writes_error else '$false'}) {{
            Write-Error 'Compose emitted controlled stderr.'
        }}
        $global:LASTEXITCODE = {compose_exit_code}
        return
    }}
    $global:LASTEXITCODE = 0
}}
function Invoke-WebRequest {{
    $status = $readyStatuses[[Math]::Min($script:readyCall, $readyStatuses.Count - 1)]
    $events.Add("ready-check:$status")
    $script:readyCall++
    [pscustomobject]@{{ StatusCode = $status }}
}}
function Start-Process {{
    $events.Add('browser-open')
}}
function Start-Sleep {{
    $events.Add('sleep')
}}

try {{
    $env:COMPOSE_PROJECT_NAME = 'foreign-owner'
    & $scriptPath -ReadyTimeoutSeconds 10 {'-Build' if build else ''} {'-ProjectName ' + project_name if project_name else ''}
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
        tmp_path,
        free_memory_kib=3 * 1024 * 1024,
        compose_exit_code=37,
        ready_status=200,
        compose_writes_error=True,
    )

    assert "Docker Compose 启动失败" in result
    assert "37" in result
    assert any("compose" in event and "up -d" in event for event in events)
    assert "browser-open" not in events


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
def test_start_script_opens_browser_only_after_ready_200(tmp_path: Path) -> None:
    result, events = _run_start_script_harness(
        tmp_path,
        free_memory_kib=3 * 1024 * 1024,
        compose_exit_code=0,
        ready_status=200,
        ready_statuses=(503, 200),
    )

    assert result == "success"
    compose_up_index = next(i for i, event in enumerate(events) if "compose" in event and "up -d" in event)
    ready_503_index = events.index("ready-check:503")
    ready_200_index = events.index("ready-check:200")
    browser_index = events.index("browser-open")
    assert compose_up_index < ready_503_index < ready_200_index < browser_index


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
@pytest.mark.parametrize(("build", "expected_suffix"), [(False, " up -d"), (True, " up -d --build")])
def test_start_script_only_adds_build_for_explicit_build_switch(
    tmp_path: Path, build: bool, expected_suffix: str
) -> None:
    result, events = _run_start_script_harness(
        tmp_path,
        free_memory_kib=3 * 1024 * 1024,
        compose_exit_code=0,
        ready_status=200,
        build=build,
    )

    assert result == "success"
    compose_up = next(event for event in events if "compose" in event and " up -d" in event)
    assert compose_up.endswith(expected_suffix)


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
def test_start_script_explicit_project_name_overrides_foreign_environment_for_every_compose_call(
    tmp_path: Path,
) -> None:
    result, events = _run_start_script_harness(
        tmp_path,
        free_memory_kib=3 * 1024 * 1024,
        compose_exit_code=0,
        ready_status=200,
        project_name="stage5launcher",
    )

    assert result == "success"
    compose_calls = [event for event in events if event.startswith("docker compose ")]
    assert compose_calls
    assert all("compose -p stage5launcher -f " in event for event in compose_calls)


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
def test_start_script_default_project_name_overrides_foreign_environment(tmp_path: Path) -> None:
    result, events = _run_start_script_harness(
        tmp_path, free_memory_kib=3 * 1024 * 1024, compose_exit_code=0, ready_status=200
    )

    assert result == "success"
    compose_calls = [event for event in events if event.startswith("docker compose ")]
    assert compose_calls
    assert all("compose -p ai-knowledge-assistant -f " in event for event in compose_calls)


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
def test_start_script_treats_daemon_stderr_as_probe_and_recovers_after_desktop_start(
    tmp_path: Path,
) -> None:
    result, events = _run_start_script_harness(
        tmp_path,
        free_memory_kib=3 * 1024 * 1024,
        compose_exit_code=0,
        ready_status=200,
        info_failures_before_ready=1,
    )

    assert result == "success"
    desktop_help_index = events.index("docker desktop --help")
    desktop_start_index = events.index("docker desktop start")
    compose_up_index = next(i for i, event in enumerate(events) if "compose" in event and "up -d" in event)
    assert events[0] == "docker info"
    assert desktop_help_index < desktop_start_index < compose_up_index
    assert events.count("docker info") >= 2
    assert "ready-check:200" in events


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
def test_start_script_accepts_compose_stderr_when_exit_code_is_zero(tmp_path: Path) -> None:
    result, events = _run_start_script_harness(
        tmp_path,
        free_memory_kib=3 * 1024 * 1024,
        compose_exit_code=0,
        ready_status=200,
        compose_writes_error=True,
    )

    assert result == "success"
    assert any("compose" in event and "up -d" in event for event in events)
    assert "browser-open" in events


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


def test_stop_script_only_stops_the_current_compose_project() -> None:
    content = _read(STOP_SCRIPT)
    lowered = content.lower()

    assert "deploy/docker-compose.yml" in content
    assert "@('info')" in content
    assert "'down', '--remove-orphans'" in content
    for forbidden in (
        "down -v",
        "docker volume",
        "volume rm",
        "docker desktop stop",
        "remove-item",
    ):
        assert forbidden not in lowered


def _run_stop_script_harness(
    tmp_path: Path,
    *,
    info_exit_code: int,
    compose_exit_code: int,
    info_writes_error: bool = False,
    compose_writes_error: bool = False,
    project_name: str | None = None,
) -> tuple[str, list[str], list[str]]:
    """在含空格的临时项目中运行停止脚本，docker 始终为受控函数替身。"""
    project_root = tmp_path / "project with spaces"
    scripts_dir = project_root / "scripts"
    deploy_dir = project_root / "deploy"
    scripts_dir.mkdir(parents=True)
    deploy_dir.mkdir()
    shutil.copy2(STOP_SCRIPT, scripts_dir / "stop-project.ps1")
    compose_file = deploy_dir / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    harness = tmp_path / "stop-launcher-harness.ps1"
    harness.write_text(
        f"""$ErrorActionPreference = 'Stop'
$events = [System.Collections.Generic.List[string]]::new()
$scriptPath = '{(scripts_dir / 'stop-project.ps1').as_posix()}'

function docker {{
    $events.Add(($args | ForEach-Object {{ "[{{0}}]" -f $_ }}) -join '')
    if ($args[0] -eq 'info') {{
        if ({'$true' if info_writes_error else '$false'}) {{
            Write-Error 'Docker daemon is unavailable (controlled stderr).'
        }}
        $global:LASTEXITCODE = {info_exit_code}
        return
    }}
    if ($args[0] -eq 'compose') {{
        if ({'$true' if compose_writes_error else '$false'}) {{
            Write-Error 'Compose emitted controlled stderr.'
        }}
        $global:LASTEXITCODE = {compose_exit_code}
        return
    }}
    $global:LASTEXITCODE = 0
}}

Push-Location '{tmp_path.as_posix()}'
try {{
    $env:COMPOSE_PROJECT_NAME = 'foreign-owner'
    & $scriptPath {'-ProjectName ' + project_name if project_name else ''}
    'RESULT:success'
}}
catch {{
    "RESULT:error:$($_.Exception.Message)"
}}
finally {{
    Pop-Location
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
    output = [line for line in lines if not line.startswith(("RESULT:", "EVENT:"))]
    return result, events, output


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
def test_stop_script_stops_only_the_absolute_current_compose_file(tmp_path: Path) -> None:
    result, events, output = _run_stop_script_harness(
        tmp_path, info_exit_code=0, compose_exit_code=0
    )
    compose_file = tmp_path / "project with spaces" / "deploy" / "docker-compose.yml"

    assert result == "success"
    assert events == [
        "[info]",
        f"[compose][-p][ai-knowledge-assistant][-f][{compose_file.resolve()}][down][--remove-orphans]",
    ]
    assert output == ["项目容器已停止；数据库、uploads 和 Hugging Face 缓存卷均已保留。"]


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
def test_stop_script_does_not_compose_when_docker_daemon_is_unavailable(tmp_path: Path) -> None:
    result, events, output = _run_stop_script_harness(
        tmp_path, info_exit_code=19, compose_exit_code=0, info_writes_error=True
    )

    assert "Docker 引擎不可用" in result
    assert events == ["[info]"]
    assert "项目容器已停止" not in "\n".join(output)


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
def test_stop_script_reports_compose_exit_code_without_success_message(tmp_path: Path) -> None:
    result, events, output = _run_stop_script_harness(
        tmp_path, info_exit_code=0, compose_exit_code=41, compose_writes_error=True
    )

    assert "Docker Compose 服务失败" in result
    assert "41" in result
    assert len(events) == 2
    assert events[1].endswith("][down][--remove-orphans]")
    assert "项目容器已停止" not in "\n".join(output)


@pytest.mark.skipif(os.name != "nt", reason="PowerShell 受控替身测试仅在 Windows 执行")
def test_stop_script_explicit_project_name_overrides_foreign_environment(tmp_path: Path) -> None:
    result, events, _ = _run_stop_script_harness(
        tmp_path, info_exit_code=0, compose_exit_code=0, project_name="stage5launcher"
    )
    compose_file = tmp_path / "project with spaces" / "deploy" / "docker-compose.yml"

    assert result == "success"
    assert events == [
        "[info]",
        f"[compose][-p][stage5launcher][-f][{compose_file.resolve()}][down][--remove-orphans]",
    ]


def test_launchers_do_not_contain_credentials() -> None:
    for path in (START_CMD, STOP_CMD, START_SCRIPT, STOP_SCRIPT):
        content = _read(path).lower()
        for forbidden in (
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
