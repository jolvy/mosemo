from typing import Any

from pydantic import BaseModel, Field


class ApiErrorResponse(BaseModel):
    """Mosemo API가 통제된 오류를 반환할 때 사용하는 응답입니다."""

    detail: str = Field(description="클라이언트에 공개하는 오류 메시지입니다.")


class ValidationErrorDetail(BaseModel):
    """요청에서 발견된 개별 입력값 검증 오류입니다."""

    loc: list[str | int] = Field(
        description="오류가 발생한 요청 영역과 필드의 경로입니다."
    )
    msg: str = Field(description="입력값이 유효하지 않은 이유입니다.")
    type: str = Field(description="검증 오류 유형을 식별하는 코드입니다.")
    input: Any | None = Field(
        default=None,
        description="검증에 실패한 입력값입니다.",
    )
    ctx: dict[str, Any] | None = Field(
        default=None,
        description="검증 오류 메시지를 구성하는 추가 정보입니다.",
    )


class RequestValidationErrorResponse(BaseModel):
    """FastAPI가 요청 입력값 검증 실패 시 반환하는 응답입니다."""

    detail: list[ValidationErrorDetail] = Field(
        description="요청에서 발견된 입력값 검증 오류 목록입니다."
    )
