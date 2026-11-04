from fastapi import APIRouter

from mosemo.activities.router import router as activities_router
from mosemo.activity_labels.catalog.router import router as label_catalog_router
from mosemo.activity_labels.router import router as labels_router
from mosemo.api.contexts import IDENTITY
from mosemo.focus_sessions.router import router as focus_sessions_router
from mosemo.shared.errors import CommonErrorCode
from mosemo.shared.openapi import api_error_responses

v1_api_router = APIRouter(
    prefix="/api/v1",
    responses=api_error_responses(
        CommonErrorCode.REQUEST_ROUTE_NOT_FOUND,
        CommonErrorCode.REQUEST_METHOD_NOT_ALLOWED,
        CommonErrorCode.INTERNAL_SERVER_ERROR,
    ),
)

v1_api_router.include_router(IDENTITY.auth_router(), prefix="/auth", tags=["auth"])
v1_api_router.include_router(IDENTITY.accounts_router(), tags=["accounts"])
v1_api_router.include_router(activities_router, tags=["activities"])
v1_api_router.include_router(labels_router, tags=["activities"])
v1_api_router.include_router(label_catalog_router, tags=["labels"])
v1_api_router.include_router(IDENTITY.devices_router(), tags=["devices"])

v1_api_router.include_router(focus_sessions_router, tags=["focus-sessions"])
