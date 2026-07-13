from fastapi import FastAPI

from app.api.error_handlers import app_error_handler
from app.api.middleware import RequestIdMiddleware
from app.api.v1.auth import router as auth_router
from app.api.v1.documents import router as document_router
from app.api.v1.health import router as health_router
from app.api.v1.knowledge_bases import router as knowledge_base_router
from app.api.v1.questions import router as question_router
from app.core.config import get_settings
from app.core.exceptions import AppError


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(knowledge_base_router)
    app.include_router(document_router)
    app.include_router(question_router)
    return app


app = create_app()
