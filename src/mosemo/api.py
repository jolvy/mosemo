from fastapi import APIRouter

from mosemo.accounts.router import router as accounts_router
from mosemo.activities.router import router as activities_router
from mosemo.auth.router import router as auth_router
from mosemo.devices.router import router as devices_router
from mosemo.exceptions import ErrorCode
from mosemo.labels.router import router as labels_router
from mosemo.openapi import api_error_responses

v1_api_router = APIRouter(
    prefix="/api/v1",
    responses=api_error_responses(
        ErrorCode.REQUEST_ROUTE_NOT_FOUND,
        ErrorCode.REQUEST_METHOD_NOT_ALLOWED,
        ErrorCode.INTERNAL_SERVER_ERROR,
    ),
)

v1_api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
v1_api_router.include_router(accounts_router, tags=["accounts"])
v1_api_router.include_router(activities_router, tags=["activities"])
v1_api_router.include_router(labels_router, tags=["activities"])
v1_api_router.include_router(devices_router, tags=["devices"])
