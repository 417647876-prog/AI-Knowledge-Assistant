import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
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
