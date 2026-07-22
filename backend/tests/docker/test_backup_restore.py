from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKUP_SCRIPT = PROJECT_ROOT / "deploy" / "backup.ps1"
RESTORE_SCRIPT = PROJECT_ROOT / "deploy" / "restore.ps1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_backup_and_restore_scripts_have_explicit_docker_and_restore_opt_in() -> None:
    backup = _read(BACKUP_SCRIPT)
    restore = _read(RESTORE_SCRIPT)

    assert "[switch]$UseDocker" in backup
    assert "[switch]$UseDocker" in restore
    assert "[switch]$ConfirmRestore" in restore
    assert "ConfirmRestore" in restore
    assert "RUN_DOCKER_TESTS" not in backup + restore


def test_backup_uses_pg_dump_and_only_copies_uploads() -> None:
    script = _read(BACKUP_SCRIPT)

    assert "pg_dump" in script
    assert "database.dump" in script
    assert "/app/uploads" in script
    assert "uploads" in script
    for forbidden in ("hf_cache", "huggingface", "docker image save", "logs"):
        assert forbidden not in script.lower()


def test_restore_validates_structure_and_uses_pg_restore_without_deleting_volumes() -> None:
    script = _read(RESTORE_SCRIPT)

    assert "manifest.json" in script
    assert "database.dump" in script
    assert "pg_restore" in script
    assert "/app/uploads" in script
    assert "down -v" not in script.lower()
    assert "docker volume rm" not in script.lower()


@pytest.mark.parametrize("script", [BACKUP_SCRIPT, RESTORE_SCRIPT])
def test_scripts_never_print_or_archive_application_credentials(script: Path) -> None:
    content = _read(script).lower()
    for forbidden in (
        "jwt_secret_key",
        "gateway_shared_secret",
        "chat_api_key",
        "embedding_api_key",
        "postgres_password",
        ".env",
    ):
        assert forbidden not in content


def test_restore_refuses_before_touching_docker_without_confirmation(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(RESTORE_SCRIPT),
            "-BackupDirectory",
            str(tmp_path),
            "-UseDocker",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode != 0
    assert "ConfirmRestore" in result.stderr
    assert "password" not in (result.stdout + result.stderr).lower()
