"""Build an API app whose identity routes use mocked use cases."""

from unittest.mock import sentinel

from fastapi import APIRouter, FastAPI

from mosemo.entrypoints.api.exception_handlers import register_exception_handlers
from mosemo.identity.bootstrap import IdentityConfig
from mosemo.platform.providers import get_session
from mosemo.shared.presentation.current_account import get_current_account
from tests.support.identity import current_account_provider


def make_identity_app(
    router: APIRouter, *, identity_config: IdentityConfig, prefix: str = ""
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix=f"/api/v1{prefix}")
    app.dependency_overrides[get_session] = lambda: sentinel.db_session
    app.dependency_overrides[get_current_account] = current_account_provider(
        identity_config
    )
    return app
