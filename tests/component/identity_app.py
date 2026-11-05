"""Build an API app whose identity routes use mocked use cases."""

from unittest.mock import sentinel

from fastapi import APIRouter, FastAPI

from mosemo.entrypoints.api.exception_handlers import register_exception_handlers
from mosemo.identity.bootstrap import get_token_current_account
from mosemo.platform.config import Config, get_config
from mosemo.platform.providers import get_session
from mosemo.shared.presentation.current_account import get_current_account


def make_identity_app(
    router: APIRouter, *, config: Config, prefix: str = ""
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix=f"/api/v1{prefix}")
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_session] = lambda: sentinel.db_session
    app.dependency_overrides[get_current_account] = get_token_current_account
    return app
