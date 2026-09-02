from fastapi import APIRouter

from mosemo.accounts.router import router as accounts_router
from mosemo.auth.router import router as auth_router

v1_api_router = APIRouter(prefix="/api/v1")

v1_api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
v1_api_router.include_router(accounts_router, tags=["accounts"])
