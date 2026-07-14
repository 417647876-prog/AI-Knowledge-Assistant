import pytest

from scripts.reset_development_data import validate_database_url, validate_reset_target


def test_reset_guard_only_accepts_confirmed_local_development() -> None:
    assert validate_reset_target("development", "localhost", confirmed=True) is None
    assert validate_reset_target("development", "LOCALHOST", confirmed=True) is None

    with pytest.raises(RuntimeError, match="仅允许 development"):
        validate_reset_target("production", "localhost", confirmed=True)
    with pytest.raises(RuntimeError, match="仅允许本地数据库"):
        validate_reset_target("development", "db.example.com", confirmed=True)
    with pytest.raises(RuntimeError, match="必须显式确认"):
        validate_reset_target("development", "localhost", confirmed=False)


def test_reset_guard_rejects_query_host_override() -> None:
    database_url = "postgresql+psycopg://u:p@localhost:5432/db?host=db.example.com&port=5433"

    with pytest.raises(RuntimeError, match="禁止通过查询参数覆盖数据库目标"):
        validate_database_url(database_url)


@pytest.mark.parametrize(
    "host",
    ["localhost", "LOCALHOST", "127.0.0.1", "[::1]"],
)
def test_reset_guard_accepts_local_driver_targets(host: str) -> None:
    url = validate_database_url(f"postgresql+psycopg://u:p@{host}:5432/db")

    assert url.database == "db"


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://u:p@/db",
        "postgresql+psycopg://u:p@db.example.com:5432/db",
    ],
)
def test_reset_guard_rejects_empty_or_remote_driver_target(database_url: str) -> None:
    with pytest.raises(RuntimeError, match="仅允许本地数据库"):
        validate_database_url(database_url)


def test_reset_guard_requires_explicit_database_name() -> None:
    with pytest.raises(RuntimeError, match="必须显式指定数据库名称"):
        validate_database_url("postgresql+psycopg://u:p@localhost:5432/")


@pytest.mark.parametrize(
    "override_key",
    [
        "HOST",
        "HostAddr",
        "PORT",
        "DbName",
        "SERVICE",
        "ServiceFile",
        "USER",
        "Password",
    ],
)
def test_reset_guard_rejects_case_insensitive_target_overrides(
    override_key: str,
) -> None:
    database_url = f"postgresql+psycopg://u:p@localhost:5432/db?{override_key}=attacker-controlled"

    with pytest.raises(RuntimeError, match="禁止通过查询参数覆盖数据库目标"):
        validate_database_url(database_url)
