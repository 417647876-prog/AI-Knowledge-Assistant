from __future__ import annotations

from pathlib import Path

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
