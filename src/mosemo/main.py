from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, TypedDict, cast

import httpx2
from fastapi import FastAPI

from mosemo.api import v1_api_router
from mosemo.database import engine
from mosemo.exception_handlers import register_exception_handlers
from mosemo.logging import configure_logging
from mosemo.middleware import RedactSensitiveQueryStringMiddleware
from mosemo.openapi import public_openapi

API_DESCRIPTION = """
Mosemo 공개 API입니다.

## 공개 JSON 및 시간 계약

- 공개 JSON 요청과 응답의 필드명은 camelCase를 사용합니다.
- 성공 응답은 공통 `data`, `result`, `success` envelope 없이 도메인 데이터를 직접 반환합니다.
- 관측 시각 요청은 `YYYY-MM-DDTHH:MM:SS[.ffffff]Z` 형식이며,
  소수 초는 0~6자리까지 사용할 수 있습니다.
- JSON 응답의 서버 시각은 UTC 정수 초 `YYYY-MM-DDTHH:MM:SSZ` 형식입니다.
  Python 값에서는 마이크로초를 보존하고 JSON 직렬화에서만 반올림 없이 절삭합니다.
- 관측 시각을 시간 블록으로 구성하는 규칙과 블록 경계는 서버가 결정합니다.

## 공개 오류 계약

- 애플리케이션이 생성하는 JSON 오류는 `error` 안에 `status`, `code`, `message`,
  `details`를 담는 공통 envelope를 사용합니다.
- `error.code`는 HTTP status와 같고, `error.status`는 클라이언트가 분기할
  애플리케이션 오류 식별자입니다. validation detail은 RequestValidationError의
  `loc`, `msg`, `type` 원문만 가집니다.
- 404·405·500은 모든 v1 public operation의 공통 JSON 오류이며, 422는 실제 요청
  입력 검증이 있는 operation에만 선언합니다.
- 애플리케이션이 처리하지 못한 예외는 `500 INTERNAL_SERVER_ERROR` envelope로
  반환하며, 원본 예외 정보는 응답에 포함하지 않습니다.
- 응답 전송이 시작된 뒤 발생한 오류, proxy가 자체 생성한 오류, OAuth
  custom-scheme redirect는 이 JSON 오류 계약의 범위 밖입니다.

macOS 생성 클라이언트 갱신은 이 서버 계약 작업의 범위 밖입니다.
""".strip()


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


configure_logging()

app = FastAPI(
    title="Mosemo API",
    description=API_DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)
app.add_middleware(RedactSensitiveQueryStringMiddleware)
app.include_router(v1_api_router)
app.openapi = cast(Any, partial(public_openapi, app))
