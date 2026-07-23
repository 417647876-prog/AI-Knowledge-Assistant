import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from queue import Queue
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, make_url
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
    database_name = f"knowledge_observability_test_{uuid4().hex}"
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
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        admin_engine.dispose()


def _alembic_config(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    config.set_main_option("path_separator", "os")
    return config


def _seed_private_scope(connection: Connection) -> tuple[UUID, UUID]:
    user_id = uuid4()
    knowledge_base_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO users (id, username, password_hash, role, is_active) "
            "VALUES (:id, :username, 'hash', 'user', true)"
        ),
        {"id": user_id, "username": f"observability_{uuid4().hex}"},
    )
    connection.execute(
        text(
            "INSERT INTO knowledge_bases (id, name, owner_id) "
            "VALUES (:id, '观测迁移知识库', :owner_id)"
        ),
        {"id": knowledge_base_id, "owner_id": user_id},
    )
    return user_id, knowledge_base_id


def _insert_conversation(connection: Connection, *, user_id: UUID, knowledge_base_id: UUID) -> UUID:
    conversation_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO conversations (id, user_id, knowledge_base_id, title) "
            "VALUES (:id, :user_id, :knowledge_base_id, '迁移后会话')"
        ),
        {
            "id": conversation_id,
            "user_id": user_id,
            "knowledge_base_id": knowledge_base_id,
        },
    )
    return conversation_id


def _insert_completed_message(
    connection: Connection,
    *,
    conversation_id: UUID,
    sequence_number: int,
    role: str,
    retry_of_message_id: UUID | None = None,
) -> UUID:
    message_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO conversation_messages "
            "(id, conversation_id, sequence_number, role, content, status, "
            "retry_of_message_id, completed_at) "
            "VALUES (:id, :conversation_id, :sequence_number, :role, '安全测试正文', "
            "'completed', :retry_of_message_id, :completed_at)"
        ),
        {
            "id": message_id,
            "conversation_id": conversation_id,
            "sequence_number": sequence_number,
            "role": role,
            "retry_of_message_id": retry_of_message_id,
            "completed_at": datetime.now(UTC),
        },
    )
    return message_id


def test_observability_migration_preserves_old_scope_and_enforces_core_contracts(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "20260716_07")
        with engine.begin() as connection:
            user_id, knowledge_base_id = _seed_private_scope(connection)

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "20260716_08"
            )
            assert (
                connection.scalar(
                    text("SELECT owner_id FROM knowledge_bases WHERE id=:id"),
                    {"id": knowledge_base_id},
                )
                == user_id
            )
            table_names = set(
                connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                        "AND tablename IN ('conversations','conversation_messages',"
                        "'llm_usage_events','answer_observations','answer_feedback',"
                        "'user_quotas','quality_evaluation_runs')"
                    )
                ).scalars()
            )
        assert table_names == {
            "conversations",
            "conversation_messages",
            "llm_usage_events",
            "answer_observations",
            "answer_feedback",
            "user_quotas",
            "quality_evaluation_runs",
        }

        with engine.begin() as connection:
            conversation_id = _insert_conversation(
                connection, user_id=user_id, knowledge_base_id=knowledge_base_id
            )
            _insert_completed_message(
                connection,
                conversation_id=conversation_id,
                sequence_number=1,
                role="user",
            )
            answer_id = _insert_completed_message(
                connection,
                conversation_id=conversation_id,
                sequence_number=2,
                role="assistant",
            )
            _insert_completed_message(
                connection,
                conversation_id=conversation_id,
                sequence_number=3,
                role="assistant",
                retry_of_message_id=answer_id,
            )
            usage_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO llm_usage_events "
                    "(id, user_id, knowledge_base_id, conversation_id, message_id, purpose, "
                    "status, model, provider_request_id, usage_complete, price_snapshot, "
                    "reserved_cost, settled_cost, completed_at) "
                    "VALUES (:id, :user_id, :knowledge_base_id, :conversation_id, "
                    ":message_id, 'answer', 'succeeded', 'deepseek-chat', :provider_request_id, "
                    "true, '{}'::jsonb, :reserved_cost, :settled_cost, :completed_at)"
                ),
                {
                    "id": usage_id,
                    "user_id": user_id,
                    "knowledge_base_id": knowledge_base_id,
                    "conversation_id": conversation_id,
                    "message_id": answer_id,
                    "provider_request_id": f"provider-{uuid4().hex}",
                    "reserved_cost": Decimal("0.123456"),
                    "settled_cost": Decimal("0.123456"),
                    "completed_at": datetime.now(UTC),
                },
            )
            first_feedback_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO answer_feedback (id, message_id, user_id, helpful) "
                    "VALUES (:id, :message_id, :user_id, true)"
                ),
                {
                    "id": first_feedback_id,
                    "message_id": answer_id,
                    "user_id": user_id,
                },
            )

        with engine.connect() as connection:
            costs = connection.execute(
                text("SELECT reserved_cost, settled_cost FROM llm_usage_events WHERE id=:id"),
                {"id": usage_id},
            ).one()
        assert costs == (Decimal("0.123456"), Decimal("0.123456"))

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                _insert_completed_message(
                    connection,
                    conversation_id=conversation_id,
                    sequence_number=2,
                    role="assistant",
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO answer_feedback (id, message_id, user_id, helpful) "
                        "VALUES (:id, :message_id, :user_id, false)"
                    ),
                    {"id": uuid4(), "message_id": answer_id, "user_id": user_id},
                )

        with engine.begin() as connection:
            other_conversation_id = _insert_conversation(
                connection, user_id=user_id, knowledge_base_id=knowledge_base_id
            )
            other_answer_id = _insert_completed_message(
                connection,
                conversation_id=other_conversation_id,
                sequence_number=1,
                role="assistant",
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                _insert_completed_message(
                    connection,
                    conversation_id=conversation_id,
                    sequence_number=4,
                    role="assistant",
                    retry_of_message_id=other_answer_id,
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                _insert_completed_message(
                    connection,
                    conversation_id=conversation_id,
                    sequence_number=4,
                    role="user",
                    retry_of_message_id=answer_id,
                )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_observability_downgrade_round_trip_when_new_tables_are_empty(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "20260716_07")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "20260716_07"
            )
            assert connection.scalar(text("SELECT to_regclass('public.conversations')")) is None

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "20260716_08"
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.llm_usage_events')"))
                == "llm_usage_events"
            )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_observability_downgrade_refuses_new_state_and_preserves_it(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            user_id, knowledge_base_id = _seed_private_scope(connection)
            conversation_id = _insert_conversation(
                connection, user_id=user_id, knowledge_base_id=knowledge_base_id
            )

        with pytest.raises(RuntimeError, match="不能无损降级"):
            command.downgrade(config, "20260716_07")

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "20260716_08"
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM conversations WHERE id=:id"),
                    {"id": conversation_id},
                )
                == 1
            )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_usage_totals_scope_and_resource_identity_are_database_enforced(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            user_id, knowledge_base_id = _seed_private_scope(connection)
            conversation_id = _insert_conversation(
                connection, user_id=user_id, knowledge_base_id=knowledge_base_id
            )
            answer_id = _insert_completed_message(
                connection,
                conversation_id=conversation_id,
                sequence_number=1,
                role="assistant",
            )

        usage_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO llm_usage_events "
                    "(id, user_id, knowledge_base_id, conversation_id, message_id, purpose, "
                    "status, model, cache_hit_input_tokens, cache_miss_input_tokens, "
                    "output_tokens, reasoning_tokens, total_tokens, usage_complete, "
                    "reserved_cost, settled_cost, completed_at) "
                    "VALUES (:id, :user_id, :knowledge_base_id, :conversation_id, "
                    ":message_id, 'answer', 'succeeded', 'deepseek-chat', 3, 5, 7, 2, 15, "
                    "true, 0, 0, :completed_at)"
                ),
                {
                    "id": usage_id,
                    "user_id": user_id,
                    "knowledge_base_id": knowledge_base_id,
                    "conversation_id": conversation_id,
                    "message_id": answer_id,
                    "completed_at": datetime.now(UTC),
                },
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO llm_usage_events "
                        "(id, user_id, knowledge_base_id, conversation_id, message_id, "
                        "purpose, status, model, cache_hit_input_tokens, "
                        "cache_miss_input_tokens, output_tokens, "
                        "total_tokens, usage_complete, reserved_cost, completed_at) "
                        "VALUES (:id, :user_id, :knowledge_base_id, :conversation_id, "
                        ":message_id, 'rewrite', 'succeeded', 'deepseek-chat', "
                        "1, 2, 3, 99, true, 0, "
                        ":completed_at)"
                    ),
                    {
                        "id": uuid4(),
                        "user_id": user_id,
                        "knowledge_base_id": knowledge_base_id,
                        "conversation_id": conversation_id,
                        "message_id": answer_id,
                        "completed_at": datetime.now(UTC),
                    },
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO llm_usage_events "
                        "(id, user_id, knowledge_base_id, conversation_id, message_id, "
                        "purpose, status, model, usage_complete, reserved_cost, completed_at) "
                        "VALUES (:id, :user_id, :knowledge_base_id, :conversation_id, "
                        ":message_id, 'rewrite', 'usage_unknown', 'deepseek-chat', true, 0, "
                        ":completed_at)"
                    ),
                    {
                        "id": uuid4(),
                        "user_id": user_id,
                        "knowledge_base_id": knowledge_base_id,
                        "conversation_id": conversation_id,
                        "message_id": answer_id,
                        "completed_at": datetime.now(UTC),
                    },
                )

        with engine.begin() as connection:
            other_user_id, other_knowledge_base_id = _seed_private_scope(connection)

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO llm_usage_events "
                        "(id, user_id, knowledge_base_id, conversation_id, message_id, "
                        "purpose, status, model, usage_complete, reserved_cost) "
                        "VALUES (:id, :user_id, :knowledge_base_id, :conversation_id, "
                        ":message_id, 'rewrite', 'reserved', 'deepseek-chat', false, 0)"
                    ),
                    {
                        "id": uuid4(),
                        "user_id": other_user_id,
                        "knowledge_base_id": other_knowledge_base_id,
                        "conversation_id": conversation_id,
                        "message_id": answer_id,
                    },
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE llm_usage_events SET user_id=:user_id WHERE id=:id"),
                    {"id": usage_id, "user_id": other_user_id},
                )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_usage_survives_explicit_conversation_content_purge(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "head")
        usage_id = uuid4()
        with engine.begin() as connection:
            user_id, knowledge_base_id = _seed_private_scope(connection)
            conversation_id = _insert_conversation(
                connection, user_id=user_id, knowledge_base_id=knowledge_base_id
            )
            message_id = _insert_completed_message(
                connection,
                conversation_id=conversation_id,
                sequence_number=1,
                role="assistant",
            )
            connection.execute(
                text(
                    "INSERT INTO llm_usage_events "
                    "(id, user_id, knowledge_base_id, conversation_id, message_id, purpose, "
                    "status, model, provider_request_id, usage_complete, reserved_cost, "
                    "settled_cost, completed_at) "
                    "VALUES (:id, :user_id, :knowledge_base_id, :conversation_id, "
                    ":message_id, 'answer', 'succeeded', 'deepseek-chat', "
                    ":provider_request_id, true, :cost, :cost, :completed_at)"
                ),
                {
                    "id": usage_id,
                    "user_id": user_id,
                    "knowledge_base_id": knowledge_base_id,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "provider_request_id": f"provider-{uuid4().hex}",
                    "cost": Decimal("0.123456"),
                    "completed_at": datetime.now(UTC),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO answer_observations "
                    "(id, user_id, knowledge_base_id, conversation_id, message_id, "
                    "was_rewritten, rewrite_fallback, candidate_count, accepted_count, "
                    "refused, citation_count, citations_valid, rewrite_ms, retrieval_ms, "
                    "generation_ms, total_ms) "
                    "VALUES (:id, :user_id, :knowledge_base_id, :conversation_id, "
                    ":message_id, false, false, 1, 1, false, 1, true, 1, 1, 1, 3)"
                ),
                {
                    "id": uuid4(),
                    "user_id": user_id,
                    "knowledge_base_id": knowledge_base_id,
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO answer_feedback (id, message_id, user_id, helpful) "
                    "VALUES (:id, :message_id, :user_id, true)"
                ),
                {"id": uuid4(), "message_id": message_id, "user_id": user_id},
            )

        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM answer_feedback WHERE message_id=:message_id"),
                {"message_id": message_id},
            )
            connection.execute(
                text("DELETE FROM answer_observations WHERE message_id=:message_id"),
                {"message_id": message_id},
            )
            connection.execute(
                text("DELETE FROM conversation_messages WHERE id=:message_id"),
                {"message_id": message_id},
            )
            connection.execute(
                text("DELETE FROM conversations WHERE id=:conversation_id"),
                {"conversation_id": conversation_id},
            )

        with engine.connect() as connection:
            retained = connection.execute(
                text(
                    "SELECT user_id, knowledge_base_id, conversation_id, message_id, "
                    "reserved_cost, settled_cost FROM llm_usage_events WHERE id=:id"
                ),
                {"id": usage_id},
            ).one()
        assert retained == (
            user_id,
            knowledge_base_id,
            conversation_id,
            message_id,
            Decimal("0.123456"),
            Decimal("0.123456"),
        )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_reasoning_tokens_cannot_exceed_output_tokens(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            user_id, knowledge_base_id = _seed_private_scope(connection)
            conversation_id = _insert_conversation(
                connection, user_id=user_id, knowledge_base_id=knowledge_base_id
            )
            message_id = _insert_completed_message(
                connection,
                conversation_id=conversation_id,
                sequence_number=1,
                role="assistant",
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO llm_usage_events "
                        "(id, user_id, knowledge_base_id, conversation_id, message_id, "
                        "purpose, status, model, output_tokens, reasoning_tokens, total_tokens, "
                        "usage_complete, reserved_cost, settled_cost, completed_at) "
                        "VALUES (:id, :user_id, :knowledge_base_id, :conversation_id, "
                        ":message_id, 'answer', 'succeeded', 'deepseek-reasoner', 1, 2, 1, "
                        "true, 0, 0, :completed_at)"
                    ),
                    {
                        "id": uuid4(),
                        "user_id": user_id,
                        "knowledge_base_id": knowledge_base_id,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "completed_at": datetime.now(UTC),
                    },
                )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_usage_completeness_matches_terminal_status_semantics(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            user_id, knowledge_base_id = _seed_private_scope(connection)
            conversation_id = _insert_conversation(
                connection, user_id=user_id, knowledge_base_id=knowledge_base_id
            )
            message_id = _insert_completed_message(
                connection,
                conversation_id=conversation_id,
                sequence_number=1,
                role="assistant",
            )

        def insert_usage(status: str, usage_complete: bool) -> None:
            completed_at = None if status == "reserved" else datetime.now(UTC)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO llm_usage_events "
                        "(id, user_id, knowledge_base_id, conversation_id, message_id, "
                        "purpose, status, model, usage_complete, reserved_cost, settled_cost, "
                        "completed_at) VALUES (:id, :user_id, :knowledge_base_id, "
                        ":conversation_id, :message_id, 'answer', :status, 'deepseek-chat', "
                        ":usage_complete, 0, 0, :completed_at)"
                    ),
                    {
                        "id": uuid4(),
                        "user_id": user_id,
                        "knowledge_base_id": knowledge_base_id,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "status": status,
                        "usage_complete": usage_complete,
                        "completed_at": completed_at,
                    },
                )

        insert_usage("failed_after_request", True)
        insert_usage("failed_after_request", False)

        invalid_pairs = (
            ("succeeded", False),
            ("reserved", True),
            ("usage_unknown", True),
            ("failed_before_request", True),
        )
        for status, usage_complete in invalid_pairs:
            with pytest.raises(IntegrityError):
                insert_usage(status, usage_complete)
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_quality_report_hash_is_unique_under_concurrent_insert(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    executor = ThreadPoolExecutor(max_workers=1)
    first_connection = None
    first_transaction = None
    try:
        command.upgrade(config, "head")
        report_hash = "a" * 64
        now = datetime.now(UTC)
        first_connection = engine.connect()
        first_transaction = first_connection.begin()
        first_connection.execute(
            text(
                "INSERT INTO quality_evaluation_runs "
                "(id, dataset_hash, mode, report_hash, gate_passed, started_at, "
                "completed_at, duration_ms) "
                "VALUES (:id, :dataset_hash, 'baseline', :report_hash, true, :now, :now, 0)"
            ),
            {"id": uuid4(), "dataset_hash": "b" * 64, "report_hash": report_hash, "now": now},
        )

        backend_pid: Queue[int] = Queue(maxsize=1)

        def insert_duplicate() -> tuple[str | None, str | None]:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                    connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                    backend_pid.put(connection.scalar(text("SELECT pg_backend_pid()")))
                    connection.execute(
                        text(
                            "INSERT INTO quality_evaluation_runs "
                            "(id, dataset_hash, mode, report_hash, gate_passed, started_at, "
                            "completed_at, duration_ms) "
                            "VALUES (:id, :dataset_hash, 'candidate', :report_hash, false, "
                            ":now, :now, 0)"
                        ),
                        {
                            "id": uuid4(),
                            "dataset_hash": "c" * 64,
                            "report_hash": report_hash,
                            "now": now,
                        },
                    )
                    transaction.commit()
                    return None, None
                except IntegrityError as exc:
                    transaction.rollback()
                    return exc.orig.sqlstate, exc.orig.diag.constraint_name
                finally:
                    if transaction.is_active:
                        transaction.rollback()

        future = executor.submit(insert_duplicate)
        pid = backend_pid.get(timeout=5)
        observed_lock_wait = False
        deadline = monotonic() + 2
        while monotonic() < deadline:
            with engine.connect() as observer:
                observed_lock_wait = (
                    observer.scalar(
                        text(
                            "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid=:pid"
                        ),
                        {"pid": pid},
                    )
                    is True
                )
            if observed_lock_wait:
                break
            sleep(0.05)

        assert observed_lock_wait is True
        first_transaction.commit()
        first_transaction = None
        sqlstate, constraint_name = future.result(timeout=10)
        assert (sqlstate, constraint_name) == (
            "23505",
            "uq_quality_evaluation_runs_report_hash",
        )
    finally:
        if first_transaction is not None and first_transaction.is_active:
            first_transaction.rollback()
        if first_connection is not None:
            first_connection.close()
        executor.shutdown(wait=True, cancel_futures=True)
        engine.dispose()
        get_settings.cache_clear()


def test_conversation_prevents_knowledge_base_owner_change(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    try:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            user_id, knowledge_base_id = _seed_private_scope(connection)
            _insert_conversation(connection, user_id=user_id, knowledge_base_id=knowledge_base_id)
            other_user_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO users (id, username, password_hash, role, is_active) "
                    "VALUES (:id, :username, 'hash', 'user', true)"
                ),
                {"id": other_user_id, "username": f"new_owner_{uuid4().hex}"},
            )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE knowledge_bases SET owner_id=:owner_id WHERE id=:id"),
                    {"id": knowledge_base_id, "owner_id": other_user_id},
                )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_conversation_and_owner_change_serialize_in_both_transaction_orders(
    temporary_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _alembic_config(temporary_database_url, monkeypatch)
    engine = create_engine(temporary_database_url)
    executor = ThreadPoolExecutor(max_workers=1)

    def wait_for_lock(pid: int) -> bool:
        deadline = monotonic() + 2
        while monotonic() < deadline:
            with engine.connect() as observer:
                if (
                    observer.scalar(
                        text(
                            "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid=:pid"
                        ),
                        {"pid": pid},
                    )
                    is True
                ):
                    return True
            sleep(0.05)
        return False

    def seed_scope() -> tuple[UUID, UUID, UUID]:
        with engine.begin() as connection:
            owner_id, knowledge_base_id = _seed_private_scope(connection)
            new_owner_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO users (id, username, password_hash, role, is_active) "
                    "VALUES (:id, :username, 'hash', 'user', true)"
                ),
                {"id": new_owner_id, "username": f"concurrent_owner_{uuid4().hex}"},
            )
        return owner_id, new_owner_id, knowledge_base_id

    try:
        command.upgrade(config, "head")

        owner_id, new_owner_id, knowledge_base_id = seed_scope()
        conversation_id = uuid4()
        insert_connection = engine.connect()
        insert_transaction = insert_connection.begin()
        try:
            insert_connection.execute(
                text(
                    "INSERT INTO conversations "
                    "(id, user_id, knowledge_base_id, title) "
                    "VALUES (:id, :user_id, :knowledge_base_id, '并发会话')"
                ),
                {
                    "id": conversation_id,
                    "user_id": owner_id,
                    "knowledge_base_id": knowledge_base_id,
                },
            )
            updater_pid: Queue[int] = Queue(maxsize=1)

            def update_owner() -> str | None:
                with engine.connect() as connection:
                    transaction = connection.begin()
                    try:
                        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                        connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                        updater_pid.put(connection.scalar(text("SELECT pg_backend_pid()")))
                        connection.execute(
                            text("UPDATE knowledge_bases SET owner_id=:owner_id WHERE id=:id"),
                            {"id": knowledge_base_id, "owner_id": new_owner_id},
                        )
                        transaction.commit()
                        return None
                    except IntegrityError as exc:
                        transaction.rollback()
                        return exc.orig.sqlstate
                    finally:
                        if transaction.is_active:
                            transaction.rollback()

            update_future = executor.submit(update_owner)
            update_waited = wait_for_lock(updater_pid.get(timeout=5))
            insert_transaction.commit()
            assert update_future.result(timeout=10) == "23514"
            assert update_waited is True
        finally:
            if insert_transaction.is_active:
                insert_transaction.rollback()
            insert_connection.close()

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT owner_id FROM knowledge_bases WHERE id=:id"),
                    {"id": knowledge_base_id},
                )
                == owner_id
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM conversations WHERE id=:id"),
                    {"id": conversation_id},
                )
                == 1
            )

        owner_id, new_owner_id, knowledge_base_id = seed_scope()
        conversation_id = uuid4()
        owner_connection = engine.connect()
        owner_transaction = owner_connection.begin()
        try:
            owner_connection.execute(
                text("UPDATE knowledge_bases SET owner_id=:owner_id WHERE id=:id"),
                {"id": knowledge_base_id, "owner_id": new_owner_id},
            )
            inserter_pid: Queue[int] = Queue(maxsize=1)

            def insert_conversation() -> str | None:
                with engine.connect() as connection:
                    transaction = connection.begin()
                    try:
                        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
                        connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                        inserter_pid.put(connection.scalar(text("SELECT pg_backend_pid()")))
                        connection.execute(
                            text(
                                "INSERT INTO conversations "
                                "(id, user_id, knowledge_base_id, title) "
                                "VALUES (:id, :user_id, :knowledge_base_id, '并发会话')"
                            ),
                            {
                                "id": conversation_id,
                                "user_id": owner_id,
                                "knowledge_base_id": knowledge_base_id,
                            },
                        )
                        transaction.commit()
                        return None
                    except IntegrityError as exc:
                        transaction.rollback()
                        return exc.orig.sqlstate
                    finally:
                        if transaction.is_active:
                            transaction.rollback()

            insert_future = executor.submit(insert_conversation)
            insert_waited = wait_for_lock(inserter_pid.get(timeout=5))
            owner_transaction.commit()
            assert insert_future.result(timeout=10) == "23514"
            assert insert_waited is True
        finally:
            if owner_transaction.is_active:
                owner_transaction.rollback()
            owner_connection.close()

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT owner_id FROM knowledge_bases WHERE id=:id"),
                    {"id": knowledge_base_id},
                )
                == new_owner_id
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM conversations WHERE id=:id"),
                    {"id": conversation_id},
                )
                == 0
            )
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        engine.dispose()
        get_settings.cache_clear()
