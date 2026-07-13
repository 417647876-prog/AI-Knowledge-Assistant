import argparse

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import Settings

LOCAL_DATABASE_HOSTS = {"localhost", "127.0.0.1", "::1"}


def validate_reset_target(app_env: str, host: str | None, *, confirmed: bool) -> None:
    if app_env != "development":
        raise RuntimeError("仅允许 development 环境重置数据")
    if host not in LOCAL_DATABASE_HOSTS:
        raise RuntimeError("仅允许本地数据库重置数据")
    if not confirmed:
        raise RuntimeError("必须显式确认数据重置")


def reset_development_data(*, app_env: str, database_url: str, confirmed: bool) -> None:
    url = make_url(database_url)
    validate_reset_target(app_env, url.host, confirmed=confirmed)

    print(
        "即将清理本地开发数据："
        f"APP_ENV={app_env}, host={url.host}, port={url.port}, database={url.database}"
    )
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE knowledge_bases CASCADE"))
    finally:
        engine.dispose()
    print("本地开发知识库数据已清理。")


def main() -> None:
    parser = argparse.ArgumentParser(description="清理本地 development 数据库中的知识库数据")
    parser.add_argument("--yes", action="store_true", help="显式确认清理")
    args = parser.parse_args()
    settings = Settings()
    reset_development_data(
        app_env=settings.app_env,
        database_url=settings.database_url,
        confirmed=args.yes,
    )


if __name__ == "__main__":
    main()
