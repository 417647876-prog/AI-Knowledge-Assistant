import pytest

from scripts.reset_development_data import validate_reset_target


def test_reset_guard_only_accepts_confirmed_local_development() -> None:
    assert validate_reset_target("development", "localhost", confirmed=True) is None

    with pytest.raises(RuntimeError, match="仅允许 development"):
        validate_reset_target("production", "localhost", confirmed=True)
    with pytest.raises(RuntimeError, match="仅允许本地数据库"):
        validate_reset_target("development", "db.example.com", confirmed=True)
    with pytest.raises(RuntimeError, match="必须显式确认"):
        validate_reset_target("development", "localhost", confirmed=False)
