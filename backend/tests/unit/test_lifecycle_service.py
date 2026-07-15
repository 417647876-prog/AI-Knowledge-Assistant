from pathlib import Path

import pytest

from app.core.exceptions import AppError
from app.lifecycle.service import resolve_upload_file


def test_resolve_upload_file_accepts_a_file_below_upload_root(tmp_path: Path) -> None:
    stored = tmp_path / "document.txt"
    stored.write_text("content", encoding="utf-8")

    assert resolve_upload_file(tmp_path, "document.txt") == stored.resolve()


@pytest.mark.parametrize("stored_name", ["../outside.txt", "sub/../../outside.txt"])
def test_resolve_upload_file_rejects_path_traversal(tmp_path: Path, stored_name: str) -> None:
    with pytest.raises(AppError) as captured:
        resolve_upload_file(tmp_path, stored_name)

    assert captured.value.code == "PURGE_PATH_INVALID"


def test_resolve_upload_file_rejects_symlink_escaping_upload_root(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = upload_root / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前 Windows 环境不允许创建符号链接")

    with pytest.raises(AppError) as captured:
        resolve_upload_file(upload_root, link.name)

    assert captured.value.code == "PURGE_PATH_INVALID"
