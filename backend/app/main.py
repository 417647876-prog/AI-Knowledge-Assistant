from fastapi import FastAPI

from app.api.error_handlers import app_error_handler
from app.api.middleware import (
    MetricsMiddleware,
    RequestIdMiddleware,
    RequestSourceMiddleware,
    UploadGuardMiddleware,
)
from app.api.v1.admin_users import router as admin_users_router
from app.api.v1.auth import router as auth_router
from app.api.v1.conversations import router as conversation_router
from app.api.v1.documents import router as document_router
from app.api.v1.feedback import router as feedback_router
from app.api.v1.health import router as health_router
from app.api.v1.internal_metrics import router as internal_metrics_router
from app.api.v1.knowledge_bases import router as knowledge_base_router
from app.api.v1.me import router as me_router
from app.api.v1.questions import router as question_router
from app.api.v1.support_content import router as support_content_router
from app.api.v1.support_grants import router as support_grants_router
from app.api.v1.trash import router as trash_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(production=settings.app_env == "production")
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(UploadGuardMiddleware, settings=settings)
    app.add_middleware(RequestSourceMiddleware, settings=settings)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.include_router(health_router)
    app.include_router(internal_metrics_router)
    app.include_router(auth_router)
    app.include_router(admin_users_router)
    app.include_router(knowledge_base_router)
    app.include_router(me_router)
    app.include_router(document_router)
    app.include_router(feedback_router)
    app.include_router(question_router)
    app.include_router(conversation_router)
    app.include_router(support_grants_router)
    app.include_router(support_content_router)
    app.include_router(trash_router)
    return app


app = create_app()
