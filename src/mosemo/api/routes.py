from fastapi import APIRouter

from mosemo.activity_labels.router import router as labels_router
from mosemo.api.activity_batches import get_batch_publisher
from mosemo.api.contexts import IDENTITY, LABELS, OBSERVATION
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
v1_api_router.include_router(
    OBSERVATION.activities_router(get_batch_publisher=get_batch_publisher),
    tags=["activities"],
)
v1_api_router.include_router(labels_router, tags=["activities"])
v1_api_router.include_router(LABELS.router(), tags=["labels"])
v1_api_router.include_router(IDENTITY.devices_router(), tags=["devices"])

v1_api_router.include_router(
    OBSERVATION.focus_sessions_router(), tags=["focus-sessions"]
)
