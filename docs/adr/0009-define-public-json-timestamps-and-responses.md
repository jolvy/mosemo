---
status: accepted
date: 2026-10-17
---
# 공개 JSON·시각 표현과 성공·오류 응답 계약을 통일한다

## 맥락

공개 API의 JSON 이름과 시각 표현이 operation마다 달라지면 클라이언트가 같은
정보를 여러 방식으로 처리해야 한다. 서버의 datetime 정밀도와 공개 표시 정밀도를
구분하고, 도메인별 성공 응답과 공통 오류 응답의 역할을 명시한다.

선택 기준은 클라이언트의 일관된 해석, 재시도·관찰 경계의 정밀도 보존과 기존
공개 계약의 유지다. 개별 operation의 오류 목록과 필드는 OpenAPI에서 관리한다.

## 결정

### JSON과 시각

- 공개 JSON 요청과 응답의 필드명은 camelCase를 사용한다. Python 내부 이름은 snake_case를 유지한다.
- JSON 요청은 camelCase alias만 허용하고 snake_case와 선언되지 않은 추가 필드는 거부한다.
- 관측 시각 요청은 UTC Z 문자열 형식이며, 소수 초는 생략하거나 1~6자리까지 사용할 수 있다. 예: `2026-09-05T01:02:03.123456Z`.
- `ObservationTimestamp`는 요청 문자열을 aware UTC datetime으로 정규화하고 마이크로초를 보존한다. `+00:00`, 다른 offset, Unix epoch 숫자는 허용하지 않는다.
- `PublicTimestamp`는 aware datetime을 Python mode에서 UTC 및 마이크로초까지 보존한다. JSON 응답에서는 UTC 정수 초 UTC Z 문자열(예: `2026-09-05T01:02:03Z`)로만 직렬화하고 소수 초는 반올림하지 않고 절삭한다.
- 관측 시각을 시간 블록으로 구성하는 로직과 블록의 시작·종료 경계는 서버 도메인이 결정한다. timestamp 타입은 이 경계를 계산하거나 보정하지 않는다.
- 같은 초 안의 관측 순서는 datetime의 표시 정밀도가 아닌 이벤트 sequence와 식별자로 보존한다.

- 관찰 타임라인의 구간 경계·lastObservedAt은 소수 초를 보존하는 도메인별
  응답 계약을 따른다. PublicTimestamp의 초 단위 표현을 모든 datetime 필드에
  일괄 적용하지 않는다.
- 같은 시각 원본의 구체적인 정렬 기준은 관찰 순서 ADR에서 정의한다. 시각
  직렬화 타입이 순서를 추정하거나 원본 시각을 보정하지 않는다.

### 성공 응답

- 성공 응답은 공통 data·result·success envelope 없이 도메인 데이터를 직접
  반환한다. success: null이나 성공 응답용 공통 wrapper를 추가하지 않는다.
- 상태 코드·본문·미디어 타입은 operation의 계약을 따른다. 본문이 없는 응답과
  redirect에 존재하지 않는 JSON 본문을 추가하지 않는다.
- redirect의 Location과 인증·캐시·cookie 등 중요한 헤더는 해당 HTTP 동작의
  계약으로 관리한다. 오류 헤더의 책임은 ADR 0001을 따른다.

### 오류 응답

- 애플리케이션이 의도적으로 반환하는 통제된 오류는 해당 라우트의 `responses`에 상태 코드, 공통 응답 모델, 미디어 타입, 설명, 예시와 중요 헤더를 명시한다.
- 모든 애플리케이션 JSON 오류는 다음 공통 envelope를 사용한다.

```json
{
  "error": {
    "status": "INVALID_ARGUMENT",
    "code": 422,
    "message": "Request validation failed.",
    "details": [
      {
        "loc": ["body", "users", 0, "email"],
        "msg": "Field required",
        "type": "missing"
      }
    ]
  }
}
```

- `error.status`는 클라이언트가 분기할 애플리케이션 오류 식별자다.
- `error.code`는 실제 HTTP response status와 같은 정수이며 `ErrorCode` member의
  `code` property에서 생성한다.
- `error.message`는 개발자용 영문 설명이다.
- `error.details`는 항상 배열이다. 기본값은 빈 배열이며 현재 `RequestValidationError` handler가 요청 검증 실패를 변환할 때 validation detail을 채운다. `ApiException`과 공통 response builder는 details 내용, 비어 있음 또는 `ErrorCode`와의 조합을 별도 정책으로 제한하지 않는다.
- 공개 오류의 종류·HTTP 상태·메시지는 [ErrorCode](../../src/mosemo/exceptions.py)를
  기준으로 관리하며, 각 operation이 반환하는 오류는 [OpenAPI 스냅샷](../../openapi/openapi.json)에서
  확인한다. 이 문서에는 오류 목록을 별도로 유지하지 않는다.
- 인증 오류는 state·쿠키·PKCE context, authorization code·verifier,
  토큰 만료·변조·삭제 계정 등 구체적인 실패 원인을 공개하지 않는다.
- 404·405·500은 v1 public operation의 공통 응답이다. 422는 실제 요청 입력 검증이 있는 operation에만 선언하며, 모든 입력이 선택적인 Kakao callback과 입력 검증이 없는 계정 조회에는 선언하지 않는다.
- 같은 HTTP status에 여러 오류가 있으면 OpenAPI 하나의 response 아래 status 이름별 `examples`로 병합한다. 모든 오류가 하나의 공통 `ErrorResponse` schema를 사용하므로 오류별 `oneOf`나 discriminator는 만들지 않는다.
- response-level description은 HTTP status phrase를 사용하고 오류별 summary와 description은 각 named example에 둔다.
- 각 example의 이름과 `error.status`는 정확히 같아야 한다.

오류 정의·응답 조립·exception handler·OpenAPI adapter의 내부 책임과 검증
상세의 변환 규칙은 ADR 0001을 따른다.

## 고려한 대안

| 대안 | 장점 | 비용·제약 |
| --- | --- | --- |
| operation마다 JSON 이름·시각 형식·응답 wrapper를 선택 | 각 기능에서 자유롭게 표현할 수 있다. | 클라이언트의 공통 처리와 계약 검증이 어려워지고 호환성 관리 범위가 늘어난다. |
| 요청에 camelCase·snake_case를 모두 허용 | 기존 Python 이름의 직접 사용이 편하다. | 공개 필드의 표현이 중복되고 alias 조합까지 검증해야 한다. |
| 모든 시각을 초 단위로 저장·정규화 | 저장값과 공개 표시가 단순해진다. | 원본 관찰 정밀도와 순간 전환 경계를 잃는다. |
| 모든 성공·오류를 같은 wrapper로 감쌈 | 최상위 형태를 통일할 수 있다. | 도메인 데이터에 불필요한 wrapper가 붙고 기존 직접 응답 계약이 바뀐다. |
| JSON 이름과 시각 타입의 책임을 통일하고 성공·오류 형태를 구분 | 기존 계약과 서버 정밀도를 유지하며 공통 해석 규칙을 제공한다. | 도메인별 정밀도 예외와 오류 계약을 구분해 검증해야 한다. |

현재 공개 계약을 유지하는 마지막 안을 채택안으로 기록한다.

## 결과와 트레이드오프

- 선언되지 않은 필드와 snake_case 요청을 거부하므로 클라이언트는 공개 alias를
  사용해야 한다. 별도의 구형 alias 호환 경로는 제공하지 않는다.
- UTC·소수 초 형식의 입력 제약은 명확하지만 다른 offset의 문자열을 보내는
  클라이언트는 UTC Z 표현으로 변환해야 한다.
- PublicTimestamp는 DB/Python 값의 마이크로초를 제거하지 않는다. JSON 정수 초
  표현만으로 실제 관찰 순서를 비교하거나 타임라인 경계를 재계산하지 않는다.
- 오류 목록은 ErrorCode와 OpenAPI를 기준으로 확인하며 ADR에 복제하지 않는다.
- 자동 계약 테스트는 JSON alias·추가 필드 거절·UTC 입력·정밀도·성공 응답 형태·
  공통 오류 envelope와 runtime OpenAPI·스냅샷의 일치를 확인한다.

## 대체한 ADR

없음. ADR 0001의 오류 경계와 ADR 0008의 관찰 순서를 유지하면서 공개 표현
계약을 보완한다.

## 연관 PRD·ADR

- [공개 오류 책임·의존 경계 ADR](0001-public-api-error-boundary.md)
- [관찰 순서 ADR](0008-order-observations-and-transition-existing-timelines.md)
- [관찰 타임라인 PRD](../prd/observation-timeline.md)
- [공통 공개 스키마](../../src/mosemo/schemas.py)
- [OpenAPI 스냅샷](../../openapi/openapi.json)

2026-10-18 사용자가 ADR 0006~0009 모두를 명시적으로 승인하여 accepted로 전환했다. 구현·검증 완료를 뜻하지 않는다.
