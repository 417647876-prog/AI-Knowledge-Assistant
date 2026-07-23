from pathlib import Path

import pytest
import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError

from app.api.v1.health import expected_migration_revision
from app.core.config import Settings

BACKEND_DIRECTORY = Path(__file__).resolve().parents[2]
PROJECT_DIRECTORY = BACKEND_DIRECTORY.parent


def _load_compose(name: str) -> dict[str, object]:
    return yaml.safe_load((PROJECT_DIRECTORY / "deploy" / name).read_text(encoding="utf-8"))


def _depends_on(service: dict[str, object], dependency: str, condition: str) -> bool:
    depends_on = service.get("depends_on", {})
    return (
        isinstance(depends_on, dict)
        and depends_on.get(dependency, {}).get("condition") == condition
    )


def _volume_targets(service: dict[str, object]) -> set[str]:
    return {
        volume.split(":", 2)[1]
        for volume in service.get("volumes", [])
        if isinstance(volume, str) and ":" in volume
    }


def test_production_compose_is_internal_gateway_architecture() -> None:
    compose = _load_compose("docker-compose.yml")
    services = compose["services"]

    assert set(services) == {"postgres", "migrate", "api", "worker", "gateway"}
    assert set(compose["volumes"]) == {
        "knowledge_postgres_data",
        "knowledge_uploads",
        "knowledge_hf_cache",
    }

    postgres = services["postgres"]
    assert postgres["image"] == "pgvector/pgvector:pg16"
    assert "ports" not in postgres
    assert "knowledge_postgres_data:/var/lib/postgresql/data" in postgres["volumes"]
    assert postgres["healthcheck"]["test"] == [
        "CMD-SHELL",
        "pg_isready -U knowledge -d knowledge",
    ]

    migrate = services["migrate"]
    assert migrate["command"] == ["alembic", "upgrade", "head"]
    assert _depends_on(migrate, "postgres", "service_healthy")

    for name in ("api", "worker"):
        service = services[name]
        assert "ports" not in service
        assert service["read_only"] is True
        assert _depends_on(service, "migrate", "service_completed_successfully")
        assert {"/app/uploads", "/home/app/.cache/huggingface"} <= _volume_targets(service)

    assert services["api"]["command"] == [
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    assert services["worker"]["command"] == ["python", "-m", "app.worker.main"]

    gateway = services["gateway"]
    assert gateway["ports"] == ["127.0.0.1:8080:80"]
    assert gateway["read_only"] is True
    assert _depends_on(gateway, "api", "service_healthy")
    assert gateway["healthcheck"]["test"] == [
        "CMD-SHELL",
        "wget -q -O /dev/null http://127.0.0.1/health && wget -q -O /dev/null http://127.0.0.1/api/ready",
    ]


def test_development_compose_only_starts_persistent_postgres() -> None:
    compose = _load_compose("docker-compose.dev.yml")

    assert set(compose["services"]) == {"postgres"}
    postgres = compose["services"]["postgres"]
    assert postgres["image"] == "pgvector/pgvector:pg16"
    assert postgres["ports"] == ["5432:5432"]
    assert "knowledge_postgres_data:/var/lib/postgresql/data" in postgres["volumes"]
    assert "healthcheck" in postgres


def test_gateway_sse_and_spa_contract_is_explicit() -> None:
    nginx = (PROJECT_DIRECTORY / "deploy" / "nginx.conf.template").read_text(encoding="utf-8")
    ready_location = nginx.split("location = /api/ready {", maxsplit=1)[1].split(
        "\n    }\n", maxsplit=1
    )[0]

    assert "location /api/" in nginx
    assert "proxy_pass http://api:8000" in nginx
    assert "proxy_http_version 1.1" in nginx
    assert "proxy_buffering off" in nginx
    assert "proxy_read_timeout 3600s" in nginx
    assert 'proxy_set_header X-Gateway-Secret "${GATEWAY_SHARED_SECRET}"' in nginx
    assert nginx.count("proxy_set_header X-Forwarded-For $remote_addr;") == 2
    assert "$proxy_add_x_forwarded_for" not in nginx
    assert "proxy_pass http://api:8000/ready;" in ready_location
    assert 'proxy_set_header X-Gateway-Secret "${GATEWAY_SHARED_SECRET}";' in ready_location
    assert "$host" in nginx
    assert "location /internal" not in nginx
    assert "location = /health" in nginx
    assert 'return 200 \'{"status":"healthy"}\'' in nginx
    assert "root /usr/share/nginx/html" in nginx
    assert "index index.html" in nginx
    assert "try_files $uri $uri/ /index.html" in nginx
    assert "client_max_body_size 21m" in nginx


def test_gateway_secret_uses_a_scoped_runtime_template_and_gateway_network() -> None:
    compose = _load_compose("docker-compose.yml")
    services = compose["services"]
    gateway = services["gateway"]

    assert gateway["environment"] == {
        "GATEWAY_SHARED_SECRET": "${GATEWAY_SHARED_SECRET:-}",
        "NGINX_ENVSUBST_FILTER": "GATEWAY_SHARED_SECRET",
    }
    assert gateway["networks"] == {
        "gateway_public": None,
        "gateway_api": {"ipv4_address": "172.28.0.10"},
    }
    assert services["api"]["networks"] == ["default", "gateway_api"]
    assert services["api"]["environment"]["TRUSTED_GATEWAY_NETWORKS"] == (
        '${TRUSTED_GATEWAY_NETWORKS:-["172.28.0.10/32"]}'
    )
    assert services["api"]["environment"]["TRUSTED_ORIGINS"] == (
        '${TRUSTED_ORIGINS:-["https://knowledge.example.com"]}'
    )
    assert compose["networks"]["gateway_public"] is None
    assert compose["networks"]["gateway_api"] == {
        "internal": True,
        "ipam": {"config": [{"subnet": "172.28.0.0/24"}]},
    }

    dockerfile = (PROJECT_DIRECTORY / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert (
        "COPY deploy/nginx.conf.template /etc/nginx/templates/default.conf.template" in dockerfile
    )
    assert "COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf" not in dockerfile


def test_production_settings_load_compose_gateway_network_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 64)
    monkeypatch.setenv("REFRESH_COOKIE_SECURE", "true")
    monkeypatch.setenv("GATEWAY_SHARED_SECRET", "gateway-secret" * 4)
    monkeypatch.setenv("TRUSTED_GATEWAY_NETWORKS", '["172.28.0.10/32"]')
    monkeypatch.setenv("TRUSTED_ORIGINS", '["https://app.example.com"]')

    settings = Settings(_env_file=None)

    assert settings.trusted_gateway_networks == ("172.28.0.10/32",)
    assert settings.trusted_origins == ["https://app.example.com"]


@pytest.mark.parametrize(
    "origins",
    [["*"], ["http://127.0.0.1:8080"], ["https://*.example.com"], ["https://app.example.com/path"]],
)
def test_production_settings_require_exact_https_trusted_origins(origins: list[str]) -> None:
    with pytest.raises(ValidationError, match="TRUSTED_ORIGINS"):
        Settings(
            _env_file=None,
            app_env="production",
            jwt_secret_key="x" * 64,
            refresh_cookie_secure=True,
            trusted_gateway_networks=("172.28.0.10/32",),
            gateway_shared_secret="gateway-secret" * 4,
            trusted_origins=origins,
        )


def test_env_example_only_contains_placeholders() -> None:
    environment = (PROJECT_DIRECTORY / "deploy" / ".env.example").read_text(encoding="utf-8")

    assert "sk-" not in environment
    assert "REPLACE_WITH" in environment


def test_backend_runtime_image_exposes_virtual_environment_commands_on_path() -> None:
    dockerfile = (BACKEND_DIRECTORY / "Dockerfile").read_text(encoding="utf-8")
    runtime_stage = dockerfile.split("FROM python:3.12-slim AS runtime", maxsplit=1)[1]

    assert 'ENV PATH="/app/.venv/bin:${PATH}"' in runtime_stage


def test_backend_runtime_image_includes_only_safe_admin_cli_script() -> None:
    dockerfile = (BACKEND_DIRECTORY / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "COPY backend/scripts/__init__.py backend/scripts/create_admin.py ./scripts/" in dockerfile
    )
    assert "COPY backend/scripts ./scripts" not in dockerfile


def test_root_build_context_has_ignore_rules_for_generated_and_secret_files() -> None:
    dockerignore = (PROJECT_DIRECTORY / ".dockerignore").read_text(encoding="utf-8")

    for ignored_path in ("**/node_modules", "**/dist", "**/.env", "**/.venv"):
        assert ignored_path in dockerignore


def test_backend_compose_injects_example_settings_with_safe_defaults() -> None:
    compose = _load_compose("docker-compose.yml")

    for name in ("migrate", "api", "worker"):
        service = compose["services"][name]
        assert service["env_file"] == [{"path": ".env", "required": False}]
        environment = service["environment"]
        assert environment["APP_ENV"] == "${APP_ENV:-production}"
        assert environment["REFRESH_COOKIE_SECURE"] == "${REFRESH_COOKIE_SECURE:-true}"
        for variable in (
            "JWT_SECRET_KEY",
            "GATEWAY_SHARED_SECRET",
            "EMBEDDING_API_KEY",
            "CHAT_API_KEY",
        ):
            assert environment[variable] == f"${{{variable}:-}}"


@pytest.mark.parametrize("secret", ["", "REPLACE_WITH_A_RANDOM_32_CHAR_SECRET"])
def test_production_settings_reject_blank_or_placeholder_jwt_secret(secret: str) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(
            _env_file=None,
            app_env="production",
            jwt_secret_key=secret,
            refresh_cookie_secure=True,
        )


@pytest.mark.parametrize("secret", ["", "REPLACE_WITH_A_RANDOM_GATEWAY_SECRET"])
def test_production_settings_reject_blank_or_placeholder_gateway_secret(secret: str) -> None:
    with pytest.raises(ValidationError, match="共享密钥"):
        Settings(
            _env_file=None,
            app_env="production",
            jwt_secret_key="x" * 64,
            refresh_cookie_secure=True,
            trusted_gateway_networks=("172.28.0.10/32",),
            gateway_shared_secret=secret,
        )


def test_expected_migration_revision_matches_alembic_head() -> None:
    config = Config(str(BACKEND_DIRECTORY / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIRECTORY / "migrations"))

    assert config.get_main_option("path_separator") == "os"
    assert expected_migration_revision() == ScriptDirectory.from_config(config).get_current_head()
