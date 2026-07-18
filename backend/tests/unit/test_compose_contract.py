from pathlib import Path

import yaml

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
    assert migrate["command"] == ["uv", "run", "alembic", "upgrade", "head"]
    assert _depends_on(migrate, "postgres", "service_healthy")

    for name in ("api", "worker"):
        service = services[name]
        assert "ports" not in service
        assert service["read_only"] is True
        assert _depends_on(service, "migrate", "service_completed_successfully")
        assert {"/app/uploads", "/home/app/.cache/huggingface"} <= _volume_targets(service)

    assert services["api"]["command"] == [
        "uv",
        "run",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    assert services["worker"]["command"] == ["uv", "run", "python", "-m", "app.worker.main"]

    gateway = services["gateway"]
    assert gateway["ports"] == ["127.0.0.1:8080:80"]
    assert gateway["read_only"] is True
    assert _depends_on(gateway, "api", "service_started")


def test_development_compose_only_starts_persistent_postgres() -> None:
    compose = _load_compose("docker-compose.dev.yml")

    assert set(compose["services"]) == {"postgres"}
    postgres = compose["services"]["postgres"]
    assert postgres["image"] == "pgvector/pgvector:pg16"
    assert postgres["ports"] == ["5432:5432"]
    assert "knowledge_postgres_data:/var/lib/postgresql/data" in postgres["volumes"]
    assert "healthcheck" in postgres


def test_gateway_sse_and_spa_contract_is_explicit() -> None:
    nginx = (PROJECT_DIRECTORY / "deploy" / "nginx.conf").read_text(encoding="utf-8")

    assert "location /api/" in nginx
    assert "proxy_pass http://api:8000" in nginx
    assert "proxy_http_version 1.1" in nginx
    assert "proxy_buffering off" in nginx
    assert "proxy_read_timeout 3600s" in nginx
    assert "location /internal" not in nginx
    assert "try_files $uri $uri/ /index.html" in nginx
    assert "client_max_body_size 21m" in nginx


def test_env_example_only_contains_placeholders() -> None:
    environment = (PROJECT_DIRECTORY / "deploy" / ".env.example").read_text(encoding="utf-8")

    assert "sk-" not in environment
    assert "REPLACE_WITH" in environment


def test_backend_runtime_image_contains_uv_for_compose_commands() -> None:
    dockerfile = (BACKEND_DIRECTORY / "Dockerfile").read_text(encoding="utf-8")
    runtime_stage = dockerfile.split("FROM python:3.12-slim AS runtime", maxsplit=1)[1]

    assert "COPY --from=uv /uv /uvx /bin/" in runtime_stage
