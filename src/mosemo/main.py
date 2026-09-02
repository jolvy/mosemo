from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TypedDict

import httpx2
from fastapi import FastAPI

from mosemo.api import v1_api_router
from mosemo.database import engine


class AppState(TypedDict):
    http_client: httpx2.AsyncClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[AppState]:
    timeout = httpx2.Timeout(10.0, connect=5.0)

    try:
        async with httpx2.AsyncClient(timeout=timeout) as client:
            yield {"http_client": client}
    finally:
        await engine.dispose()


app = FastAPI(lifespan=lifespan)

app.include_router(v1_api_router)
