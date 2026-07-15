import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from time import monotonic, sleep
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.fixture
def temporary_database_url() -> Iterator[str]:
    configured_url = make_url(Settings().database_url)
    database_name = f"knowledge_private_lifecycle_test_{uuid4().hex}"
    admin_engine = create_engine(
        configured_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    try:
        yield configured_url.set(database=database_name).render_as_string(hide_password=False)
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


def _alembic_config(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    config.set_main_option("path_separator", "os")
    return config


def _assert_backend_waiting_for_lock(engine: Engine, backend_pid: int) -> None:
    deadline = monotonic() + 2
    while monotonic() < deadline:
        with engine.connect() as connection:
            wait_event_type = connection.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),
                {"pid": backend_pid},
            )
        if wait_event_type == "Lock":
            return
        sleep(0.02)
    raise AssertionError(f"数据库连接 {backend_pid} 未进入锁等待状态")


def _insert_legacy_private_data(engine) -> dict[str, object]:
    ids: dict[str, object] = {
        "owner_id": uuid4(),
        "admin_id": uuid4(),
        "other_user_id": uuid4(),
        "knowledge_base_id": uuid4(),
        "document_id": uuid4(),
        "file_hash": uuid4().hex * 2,
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash, role, is_active) VALUES "
                "(:owner_id, 'lifecycle_owner', 'hash', 'user', true), "
                "(:admin_id, 'lifecycle_admin', 'hash', 'admin', true), "
                "(:other_user_id, 'lifecycle_other', 'hash', 'user', true)"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases (id, name, owner_id) "
                "VALUES (:knowledge_base_id, '生命周期迁移知识库', :owner_id)"
            ),
            ids,
        )
        connection.execute(
            text(
                "INSERT INTO documents "
                "(id, knowledge_base_id, original_file_name, stored_file_name, content_type, "
                "file_extension, file_size, file_hash, status) VALUES "
                "(:document_id, :knowledge_base_id, '旧文档.txt', 'legacy.txt', "
                "'text/plain', '.txt', 8, :file_hash, 'ready')"
            ),
            ids,
        )
    return ids


def test_upgrade_backfills_uploader_and_replaces_global_document_uniqueness(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "20260716_06")
        ids = _insert_legacy_private_data(engine)

        command.upgrade(config, "20260716_07")

        with engine.connect() as connection:
            uploaded_by_user_id = connection.scalar(
                text("SELECT uploaded_by_user_id FROM documents WHERE id=:document_id"), ids
            )
            uploader_nullable = connection.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='documents' "
                    "AND column_name='uploaded_by_user_id'"
                )
            )
            partial_index = connection.scalar(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
                    "AND indexname='uq_documents_active_knowledge_base_file_hash'"
                )
            )
            legacy_constraint = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_constraint WHERE conrelid='documents'::regclass "
                    "AND conname='uq_documents_knowledge_base_id_file_hash'"
                )
            )
            lifecycle_columns = {
                (row.table_name, row.column_name)
                for row in connection.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND ((table_name='knowledge_bases' "
                        "AND column_name IN ('deleted_at','purge_after')) OR "
                        "(table_name='documents' AND column_name IN "
                        "('deleted_at','purge_after','uploaded_by_user_id')))"
                    )
                )
            }

        assert uploaded_by_user_id == ids["owner_id"]
        assert uploader_nullable == "NO"
        assert partial_index is not None
        assert "UNIQUE" in partial_index
        assert "WHERE (deleted_at IS NULL)" in partial_index
        assert legacy_constraint == 0
        assert lifecycle_columns == {
            ("knowledge_bases", "deleted_at"),
            ("knowledge_bases", "purge_after"),
            ("documents", "uploaded_by_user_id"),
            ("documents", "deleted_at"),
            ("documents", "purge_after"),
        }

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO documents "
                        "(id, knowledge_base_id, uploaded_by_user_id, original_file_name, "
                        "stored_file_name, content_type, file_extension, file_size, file_hash, "
                        "status) VALUES (:new_id, :knowledge_base_id, :owner_id, '重复.txt', "
                        "'duplicate.txt', 'text/plain', '.txt', 8, :file_hash, 'ready')"
                    ),
                    {**ids, "new_id": uuid4()},
                )

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE documents SET deleted_at=now(), purge_after=now()+interval '7 days' "
                    "WHERE id=:document_id"
                ),
                ids,
            )
            replacement_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO documents "
                    "(id, knowledge_base_id, uploaded_by_user_id, original_file_name, "
                    "stored_file_name, content_type, file_extension, file_size, file_hash, "
                    "status) VALUES (:new_id, :knowledge_base_id, :owner_id, '替代.txt', "
                    "'replacement.txt', 'text/plain', '.txt', 8, :file_hash, 'ready')"
                ),
                {**ids, "new_id": replacement_id},
            )
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM documents WHERE file_hash=:file_hash"), ids
                )
                == 2
            )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_support_grant_enforces_role_owner_validity_read_only_and_non_overlap(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "20260716_06")
        ids = _insert_legacy_private_data(engine)
        command.upgrade(config, "20260716_07")
        created_at = datetime.now(UTC)
        expires_at = created_at + timedelta(minutes=30)

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO support_access_grants "
                    "(id, knowledge_base_id, owner_user_id, admin_user_id, expires_at) "
                    "VALUES (:grant_id, :knowledge_base_id, :owner_id, :admin_id, "
                    "clock_timestamp()+interval '100 milliseconds')"
                ),
                {**ids, "grant_id": uuid4()},
            )
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_sleep(0.2)"))
        with engine.begin() as connection:
            grant_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO support_access_grants "
                    "(id, knowledge_base_id, owner_user_id, admin_user_id, expires_at, "
                    "created_at) VALUES (:grant_id, :knowledge_base_id, :owner_id, :admin_id, "
                    ":expires_at, :created_at)"
                ),
                {**ids, "grant_id": grant_id, "expires_at": expires_at, "created_at": created_at},
            )
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT access_level FROM support_access_grants WHERE id=:grant_id"),
                    {"grant_id": grant_id},
                )
                == "read_only"
            )

        invalid_cases = [
            ({"admin_id": ids["other_user_id"]}, "管理员角色"),
            ({"owner_id": ids["admin_id"]}, "所属用户不能等于管理员"),
            ({"expires_at": created_at}, "过期时间"),
            ({"access_level": "write"}, "只读"),
        ]
        for overrides, _case in invalid_cases:
            values = {
                **ids,
                "grant_id": uuid4(),
                "expires_at": expires_at,
                "created_at": created_at,
                "access_level": "read_only",
                **overrides,
            }
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO support_access_grants "
                            "(id, knowledge_base_id, owner_user_id, admin_user_id, access_level, "
                            "expires_at, created_at) VALUES (:grant_id, :knowledge_base_id, "
                            ":owner_id, :admin_id, :access_level, :expires_at, :created_at)"
                        ),
                        values,
                    )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO support_access_grants "
                        "(id, knowledge_base_id, owner_user_id, admin_user_id, expires_at, "
                        "created_at) VALUES (:grant_id, :knowledge_base_id, :owner_id, :admin_id, "
                        ":expires_at, :created_at)"
                    ),
                    {
                        **ids,
                        "grant_id": uuid4(),
                        "expires_at": created_at + timedelta(minutes=45),
                        "created_at": created_at + timedelta(minutes=15),
                    },
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("UPDATE users SET role='user' WHERE id=:admin_id"), ids)
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE knowledge_bases SET owner_id=:other_user_id "
                        "WHERE id=:knowledge_base_id"
                    ),
                    ids,
                )

        with engine.begin() as connection:
            connection.execute(
                text("UPDATE support_access_grants SET revoked_at=now() WHERE id=:grant_id"),
                {"grant_id": grant_id},
            )
            connection.execute(text("UPDATE users SET role='user' WHERE id=:admin_id"), ids)
            connection.execute(text("UPDATE users SET role='admin' WHERE id=:admin_id"), ids)
            connection.execute(
                text(
                    "UPDATE knowledge_bases SET owner_id=:other_user_id WHERE id=:knowledge_base_id"
                ),
                ids,
            )
            connection.execute(
                text("UPDATE knowledge_bases SET owner_id=:owner_id WHERE id=:knowledge_base_id"),
                ids,
            )
            connection.execute(
                text(
                    "INSERT INTO support_access_grants "
                    "(id, knowledge_base_id, owner_user_id, admin_user_id, expires_at, "
                    "created_at) VALUES (:grant_id, :knowledge_base_id, :owner_id, :admin_id, "
                    ":expires_at, :created_at)"
                ),
                {
                    **ids,
                    "grant_id": uuid4(),
                    "expires_at": expires_at,
                    "created_at": created_at,
                },
            )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_expired_grant_cannot_be_extended_after_admin_loses_role(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "20260716_06")
        ids = _insert_legacy_private_data(engine)
        command.upgrade(config, "20260716_07")
        now = datetime.now(UTC)
        grant_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO support_access_grants "
                    "(id, knowledge_base_id, owner_user_id, admin_user_id, expires_at) "
                    "VALUES (:grant_id, :knowledge_base_id, :owner_id, :admin_id, "
                    "clock_timestamp()+interval '100 milliseconds')"
                ),
                {**ids, "grant_id": grant_id},
            )
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_sleep(0.2)"))
        with engine.begin() as connection:
            connection.execute(text("UPDATE users SET role='user' WHERE id=:admin_id"), ids)

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE support_access_grants SET expires_at=:expires_at WHERE id=:grant_id"
                    ),
                    {"grant_id": grant_id, "expires_at": now + timedelta(minutes=30)},
                )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_revoked_grant_cannot_be_reactivated(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "20260716_06")
        ids = _insert_legacy_private_data(engine)
        command.upgrade(config, "20260716_07")
        now = datetime.now(UTC)
        grant_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO support_access_grants "
                    "(id, knowledge_base_id, owner_user_id, admin_user_id, expires_at, "
                    "revoked_at, created_at) VALUES (:grant_id, :knowledge_base_id, "
                    ":owner_id, :admin_id, :expires_at, :revoked_at, :created_at)"
                ),
                {
                    **ids,
                    "grant_id": grant_id,
                    "created_at": now,
                    "expires_at": now + timedelta(minutes=30),
                    "revoked_at": now + timedelta(minutes=1),
                },
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE support_access_grants SET revoked_at=NULL WHERE id=:grant_id"),
                    {"grant_id": grant_id},
                )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_database_overrides_future_grant_created_at(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "20260716_06")
        ids = _insert_legacy_private_data(engine)
        command.upgrade(config, "20260716_07")
        requested_created_at = datetime.now(UTC) + timedelta(days=1)
        grant_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO support_access_grants "
                    "(id, knowledge_base_id, owner_user_id, admin_user_id, expires_at, "
                    "created_at) VALUES (:grant_id, :knowledge_base_id, :owner_id, :admin_id, "
                    ":expires_at, :created_at)"
                ),
                {
                    **ids,
                    "grant_id": grant_id,
                    "created_at": requested_created_at,
                    "expires_at": requested_created_at + timedelta(minutes=30),
                },
            )
        with engine.connect() as connection:
            created_at, database_now = connection.execute(
                text(
                    "SELECT created_at, clock_timestamp() "
                    "FROM support_access_grants WHERE id=:grant_id"
                ),
                {"grant_id": grant_id},
            ).one()

        assert created_at <= database_now
        assert created_at != requested_created_at
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("UPDATE users SET role='user' WHERE id=:admin_id"), ids)
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE knowledge_bases SET owner_id=:other_user_id "
                        "WHERE id=:knowledge_base_id"
                    ),
                    ids,
                )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_grant_created_at_cannot_be_changed(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "20260716_06")
        ids = _insert_legacy_private_data(engine)
        command.upgrade(config, "20260716_07")
        grant_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO support_access_grants "
                    "(id, knowledge_base_id, owner_user_id, admin_user_id, expires_at) "
                    "VALUES (:grant_id, :knowledge_base_id, :owner_id, :admin_id, "
                    "clock_timestamp()+interval '30 minutes')"
                ),
                {**ids, "grant_id": grant_id},
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE support_access_grants "
                        "SET created_at=created_at-interval '1 minute' WHERE id=:grant_id"
                    ),
                    {"grant_id": grant_id},
                )
    finally:
        engine.dispose()
        get_settings.cache_clear()


@pytest.mark.parametrize("protected_change", ["admin_role", "knowledge_base_owner"])
def test_protected_scope_change_uses_wall_clock_not_transaction_start(
    temporary_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    protected_change: str,
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    stale_connection = None
    stale_transaction = None
    try:
        command.upgrade(config, "20260716_06")
        ids = _insert_legacy_private_data(engine)
        command.upgrade(config, "20260716_07")
        stale_connection = engine.connect()
        stale_transaction = stale_connection.begin()
        stale_connection.execute(text("SELECT now()"))
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_sleep(0.05)"))
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO support_access_grants "
                    "(id, knowledge_base_id, owner_user_id, admin_user_id, expires_at) "
                    "VALUES (:grant_id, :knowledge_base_id, :owner_id, :admin_id, "
                    "clock_timestamp()+interval '30 minutes')"
                ),
                {**ids, "grant_id": uuid4()},
            )

        statement = (
            text("UPDATE users SET role='user' WHERE id=:admin_id")
            if protected_change == "admin_role"
            else text(
                "UPDATE knowledge_bases SET owner_id=:other_user_id WHERE id=:knowledge_base_id"
            )
        )
        with pytest.raises(IntegrityError):
            stale_connection.execute(statement, ids)
    finally:
        if stale_transaction is not None and stale_transaction.is_active:
            stale_transaction.rollback()
        if stale_connection is not None:
            stale_connection.close()
        engine.dispose()
        get_settings.cache_clear()


def test_concurrent_overlapping_grants_are_serialized_and_one_is_rejected(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    first_connection = None
    first_transaction = None
    try:
        command.upgrade(config, "20260716_06")
        ids = _insert_legacy_private_data(engine)
        command.upgrade(config, "20260716_07")
        insert_grant = text(
            "INSERT INTO support_access_grants "
            "(id, knowledge_base_id, owner_user_id, admin_user_id, expires_at) "
            "VALUES (:grant_id, :knowledge_base_id, :owner_id, :admin_id, "
            "clock_timestamp()+interval '30 minutes')"
        )
        first_connection = engine.connect()
        first_transaction = first_connection.begin()
        first_connection.execute(insert_grant, {**ids, "grant_id": uuid4()})
        second_backend_pid: Queue[int] = Queue()

        def insert_competing_grant() -> None:
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                second_backend_pid.put(connection.scalar(text("SELECT pg_backend_pid()")))
                connection.execute(insert_grant, {**ids, "grant_id": uuid4()})

        with ThreadPoolExecutor(max_workers=1) as executor:
            competing = executor.submit(insert_competing_grant)
            _assert_backend_waiting_for_lock(engine, second_backend_pid.get(timeout=2))
            first_transaction.commit()
            with pytest.raises(IntegrityError):
                competing.result(timeout=10)
    finally:
        if first_transaction is not None and first_transaction.is_active:
            first_transaction.rollback()
        if first_connection is not None:
            first_connection.close()
        engine.dispose()
        get_settings.cache_clear()


@pytest.mark.parametrize("protected_change", ["admin_role", "knowledge_base_owner"])
def test_concurrent_grant_creation_serializes_protected_scope_change(
    temporary_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    protected_change: str,
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    grant_connection = None
    grant_transaction = None
    try:
        command.upgrade(config, "20260716_06")
        ids = _insert_legacy_private_data(engine)
        command.upgrade(config, "20260716_07")
        grant_connection = engine.connect()
        grant_transaction = grant_connection.begin()
        grant_connection.execute(
            text(
                "INSERT INTO support_access_grants "
                "(id, knowledge_base_id, owner_user_id, admin_user_id, expires_at) "
                "VALUES (:grant_id, :knowledge_base_id, :owner_id, :admin_id, "
                "clock_timestamp()+interval '30 minutes')"
            ),
            {**ids, "grant_id": uuid4()},
        )
        protected_change_backend_pid: Queue[int] = Queue()

        def change_protected_scope() -> None:
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                protected_change_backend_pid.put(connection.scalar(text("SELECT pg_backend_pid()")))
                statement = (
                    text("UPDATE users SET role='user' WHERE id=:admin_id")
                    if protected_change == "admin_role"
                    else text(
                        "UPDATE knowledge_bases SET owner_id=:other_user_id "
                        "WHERE id=:knowledge_base_id"
                    )
                )
                connection.execute(statement, ids)

        with ThreadPoolExecutor(max_workers=1) as executor:
            role_change = executor.submit(change_protected_scope)
            _assert_backend_waiting_for_lock(engine, protected_change_backend_pid.get(timeout=2))
            grant_transaction.commit()
            with pytest.raises(IntegrityError):
                role_change.result(timeout=10)
    finally:
        if grant_transaction is not None and grant_transaction.is_active:
            grant_transaction.rollback()
        if grant_connection is not None:
            grant_connection.close()
        engine.dispose()
        get_settings.cache_clear()


def test_business_foreign_keys_do_not_physically_cascade_and_audit_resource_is_not_a_fk(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "20260716_07")
        with engine.connect() as connection:
            cascading_business_foreign_keys = (
                connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE contype='f' AND confrelid IN "
                        "('knowledge_bases'::regclass, 'documents'::regclass) "
                        "AND confdeltype='c'"
                    )
                )
                .scalars()
                .all()
            )
            audit_resource_foreign_keys = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_constraint c "
                    "JOIN unnest(c.conkey) AS key(attnum) ON true "
                    "JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=key.attnum "
                    "WHERE c.contype='f' AND c.conrelid='audit_events'::regclass "
                    "AND a.attname='resource_id'"
                )
            )
        assert cascading_business_foreign_keys == []
        assert audit_resource_foreign_keys == 0
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_empty_lifecycle_schema_supports_downgrade_and_reupgrade(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "20260716_07")
        command.downgrade(config, "20260716_06")
        with engine.connect() as connection:
            downgraded_version = connection.scalar(text("SELECT version_num FROM alembic_version"))
            lifecycle_column_count = connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema='public' AND ((table_name='knowledge_bases' "
                    "AND column_name IN ('deleted_at','purge_after')) OR "
                    "(table_name='documents' AND column_name IN "
                    "('uploaded_by_user_id','deleted_at','purge_after')))"
                )
            )
            lifecycle_table_count = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname='public' "
                    "AND tablename IN ('support_access_grants','audit_events')"
                )
            )
            legacy_unique_count = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_constraint "
                    "WHERE conrelid='documents'::regclass "
                    "AND conname='uq_documents_knowledge_base_id_file_hash'"
                )
            )
        assert downgraded_version == "20260716_06"
        assert lifecycle_column_count == 0
        assert lifecycle_table_count == 0
        assert legacy_unique_count == 1

        command.upgrade(config, "20260716_07")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "20260716_07"
            )
    finally:
        engine.dispose()
        get_settings.cache_clear()


@pytest.mark.parametrize("new_state", ["recycled_document", "active_grant"])
def test_downgrade_rejects_new_lifecycle_state_without_losing_it(
    temporary_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    new_state: str,
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "20260716_06")
        ids = _insert_legacy_private_data(engine)
        command.upgrade(config, "20260716_07")
        with engine.begin() as connection:
            if new_state == "recycled_document":
                connection.execute(
                    text(
                        "UPDATE documents SET deleted_at=now(), "
                        "purge_after=now()+interval '7 days' WHERE id=:document_id"
                    ),
                    ids,
                )
            else:
                connection.execute(
                    text(
                        "INSERT INTO support_access_grants "
                        "(id, knowledge_base_id, owner_user_id, admin_user_id, expires_at) "
                        "VALUES (:grant_id, :knowledge_base_id, :owner_id, :admin_id, "
                        "now()+interval '30 minutes')"
                    ),
                    {**ids, "grant_id": uuid4()},
                )

        with pytest.raises(RuntimeError, match="不能无损降级"):
            command.downgrade(config, "20260716_06")

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "20260716_07"
            )
            if new_state == "recycled_document":
                assert (
                    connection.scalar(
                        text("SELECT deleted_at IS NOT NULL FROM documents WHERE id=:document_id"),
                        ids,
                    )
                    is True
                )
            else:
                assert connection.scalar(text("SELECT count(*) FROM support_access_grants")) == 1
    finally:
        engine.dispose()
        get_settings.cache_clear()
