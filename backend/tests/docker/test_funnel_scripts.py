from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
START_SCRIPT = PROJECT_ROOT / "deploy" / "start-funnel.ps1"
STOP_SCRIPT = PROJECT_ROOT / "deploy" / "stop-funnel.ps1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("script", "required_switch"),
    [(START_SCRIPT, "ConfirmEnable"), (STOP_SCRIPT, "ConfirmDisable")],
)
def test_funnel_scripts_refuse_without_explicit_confirmation(
    script: Path,
    required_switch: str,
) -> None:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode != 0
    assert required_switch in result.stderr


def test_start_checks_gateway_and_tailscale_before_background_funnel() -> None:
    script = _read(START_SCRIPT)

    assert "docker compose" in script
    assert "/api/ready" in script
    assert "tailscale status --json" in script
    assert "BackendState" in script
    assert "funnel status --json" in script
    assert "--bg" in script
    assert "--yes" in script
    assert '"--https=$FunnelHttpsPort"' in script
    assert "http://127.0.0.1:$LocalPort" in script
    assert ".stage5-funnel.json" in script


def test_stop_removes_only_the_owned_mapping() -> None:
    script = _read(STOP_SCRIPT)

    assert ".stage5-funnel.json" in script
    assert "funnel status --json" in script
    assert "off" in script
    assert "target" in script
    for forbidden in (
        "funnel reset",
        "tailscale down",
        "docker compose down",
        "docker volume",
        "down -v",
    ):
        assert forbidden not in script.lower()


@pytest.mark.parametrize("script", [START_SCRIPT, STOP_SCRIPT])
def test_funnel_scripts_do_not_print_credentials(script: Path) -> None:
    content = _read(script).lower()
    for forbidden in (
        "jwt_secret_key",
        "gateway_shared_secret",
        "chat_api_key",
        "embedding_api_key",
        "initial_admin_password",
        ".env",
    ):
        assert forbidden not in content
