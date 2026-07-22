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
        "2MB",
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
