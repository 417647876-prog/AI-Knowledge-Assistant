from pathlib import Path

import yaml


def test_worker_compose_healthcheck_uses_task2_heartbeat_command() -> None:
    project_directory = Path(__file__).resolve().parents[3]
    compose = yaml.safe_load(
        (project_directory / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    )

    assert compose["services"]["worker"]["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "app.worker.health",
        "--max-age-seconds",
        "60",
    ]
