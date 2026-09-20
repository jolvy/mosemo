# API 문서화 규칙

Mosemo API의 구현, OpenAPI 선언, 계약 테스트는 하나의 공개 계약으로 관리한다.
라우트를 추가하거나 기존 계약을 변경할 때는 아래 규칙을 모두 적용한다.

## 스키마와 입력

- 모든 Request/Response 모델에 모델의 목적을 설명한다.
- Request/Response 모델의 모든 필드에 의미, 단위, 형식, 허용값 등 클라이언트가 사용에 필요한 설명을 작성한다.
- Query, Path, Header, Cookie, Body 입력에는 각각의 역할과 제약 조건을 설명한다. Body 입력은 요청 모델과 각 필드의 설명을 통해 문서화한다.
- 인증처럼 여러 입력이 함께 동작하는 경우에는 라우트 설명에 입력 사이의 관계와 처리 목적도 작성한다.

## 공개 JSON 및 시간 계약

- 공개 JSON 요청과 응답의 필드명은 camelCase를 사용한다. Python 내부 이름은 snake_case를 유지한다.
- JSON 요청은 camelCase alias만 허용하고 snake_case와 선언되지 않은 추가 필드는 거부한다.
- 관측 시각 요청은 `YYYY-MM-DDTHH:MM:SS[.ffffff]Z` 형식이며, 소수 초는 생략하거나 1~6자리까지 사용할 수 있다. 예: `2026-09-05T01:02:03.123456Z`.
- `ObservationTimestamp`는 요청 문자열을 aware UTC datetime으로 정규화하고 마이크로초를 보존한다. `+00:00`, 다른 offset, Unix epoch 숫자는 허용하지 않는다.
- `PublicTimestamp`는 aware datetime을 Python mode에서 UTC 및 마이크로초까지 보존한다. JSON 응답에서는 UTC 정수 초 `YYYY-MM-DDTHH:MM:SSZ`로만 직렬화하고 소수 초는 반올림하지 않고 절삭한다.
- 관측 시각을 시간 블록으로 구성하는 로직과 블록의 시작·종료 경계는 서버 도메인이 결정한다. timestamp 타입은 이 경계를 계산하거나 보정하지 않는다.
- 같은 초 안의 관측 순서는 datetime의 표시 정밀도가 아닌 이벤트 sequence와 식별자로 보존한다.

## 성공 응답

- 성공 응답은 공통 `data`, `result`, `success` envelope 없이 해당 도메인 데이터를 직접 반환한다. `success: null`과 성공 응답용 공통 wrapper를 추가하지 않는다.
- 각 라우트의 성공 상태 코드를 실제 구현과 동일하게 선언한다.
- 응답의 미디어 타입을 명시한다. 본문이 없는 응답에는 존재하지 않는 응답 본문이나 미디어 타입을 선언하지 않는다.
- 리다이렉트 응답은 상태 코드와 이동 목적을 설명하고 `Location` 헤더의 의미를 명시한다.
- `Set-Cookie`, `Cache-Control`, `Pragma`, `WWW-Authenticate`처럼 클라이언트 동작이나 보안에 영향을 주는 중요 헤더를 명시한다.
- 자동 계약 테스트는 204·205와 3xx 응답에 본문이 선언되지 않았는지, 그 밖의 성공 응답에 미디어 타입과 schema가 있는지 검증한다. 리다이렉트 응답의 `Location`과 인증 흐름의 중요 헤더도 같은 테스트에서 검증한다.

## 통제된 오류 응답

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
- 현재 공개 오류는 다음과 같다.

| status | HTTP code | message |
| --- | ---: | --- |
| `AUTH_INVALID_AUTHORIZATION_CODE` | 400 | `Invalid or expired authorization code` |
| `AUTH_INVALID_OAUTH_CONTEXT` | 400 | `Invalid or expired OAuth login context` |
| `AUTH_INVALID_ACCESS_TOKEN` | 401 | `Invalid or expired access token` |
| `ACTIVITY_DEVICE_NOT_FOUND` | 404 | `Activity device not found` |
| `ACTIVITY_EVENT_ID_CONFLICT` | 409 | `Activity event ID conflicts with a stored record` |
| `ACTIVITY_SEQUENCE_CONFLICT` | 409 | `Activity sequence conflicts with a stored record` |
| `ACTIVITY_TIMELINE_BUSY` | 503 | `Activity timeline is busy` |
| `REQUEST_ROUTE_NOT_FOUND` | 404 | `API route not found` |
| `REQUEST_METHOD_NOT_ALLOWED` | 405 | `Method not allowed` |
| `INVALID_ARGUMENT` | 422 | `Request validation failed.` |
| `INTERNAL_SERVER_ERROR` | 500 | `Internal server error` |

- 인증 오류의 공개 status는 `AUTH_INVALID_OAUTH_CONTEXT`, `AUTH_INVALID_AUTHORIZATION_CODE`, `AUTH_INVALID_ACCESS_TOKEN`이다. state·쿠키·PKCE context, authorization code·verifier, 토큰 만료·변조·삭제 계정 중 구체적인 실패 원인은 공개하지 않는다.
- 404·405·500은 v1 public operation의 공통 응답이다. 422는 실제 요청 입력 검증이 있는 operation에만 선언하며, 모든 입력이 선택적인 Kakao callback과 입력 검증이 없는 계정 조회에는 선언하지 않는다.
- 같은 HTTP status에 여러 오류가 있으면 OpenAPI 하나의 response 아래 status 이름별 `examples`로 병합한다. 모든 오류가 하나의 공통 `ErrorResponse` schema를 사용하므로 오류별 `oneOf`나 discriminator는 만들지 않는다.
- response-level description은 HTTP status phrase를 사용하고 오류별 summary와 description은 각 named example에 둔다.
- 각 example의 이름과 `error.status`는 정확히 같아야 한다.

## 요청 검증 detail

`RequestValidationError`를 처리할 때 `errors()`의 순서와 각 오류의 `loc`, `msg`, `type`을 그대로 사용한다. 공개 detail에는 이 세 필드만 포함한다.

- `loc`은 Pydantic validation error의 location tuple 원문이다. JSON에서는 문자열과 정수 segment의 배열로 직렬화하며 `body`, `query`, `path`, `header`, `cookie`, 배열 index, union branch와 모델명 segment를 제거하거나 변환하지 않는다.
- `msg`는 Pydantic validation error의 사용자용 message 원문이다. custom validator message도 그대로 공개될 수 있으므로 validator message에 비밀값이나 내부 구현 정보를 넣지 않는다.
- `type`은 Pydantic validation error의 type 원문이다. 대소문자나 의미를 변환하지 않으며 OpenAPI에서 닫힌 enum으로 표현하지 않는다.
- Pydantic의 `input`, `ctx`, `url`과 그 밖의 오류 metadata는 공개하지 않는다.
- JSON 문법 오류도 framework가 제공한 `loc`, `msg`, `type`을 그대로 사용하므로 parser offset이 `loc`의 정수 segment로 포함될 수 있다.
- Pydantic에 도달하기 전 발생한 잘못된 요청 문자 인코딩은 같은 detail 구조로 `loc: ["body"]`, `msg: "Invalid request body encoding"`, `type: "invalid_encoding"`을 반환한다.
- `loc`, `msg`, `type` 중 하나가 없거나 오류 목록이 비어 있으면 값을 임의로 보완하지 않고 예기치 않은 내부 오류로 처리한다.
- 이전 `location`, `field`, `reason`, `fieldValue` 형식은 제공하지 않는다.

## 오류 처리와 책임 경계

- `exceptions.py`의 `ErrorCode` Enum이 공개 오류 정의를 소유한다. 각 `ErrorCode.MEMBER`는 Enum 이름에서 파생한 `status`와 value의 `code`, `message` property를 제공하며, value인 frozen `ErrorSpec`은 HTTP `code`와 개발자용 `message`만 보유하고 OpenAPI metadata를 포함하지 않는다. 내부 타입 선언은 런타임에 중복 검증하지 않고 실제 member 값을 계약 테스트로 검증한다.
- `ApiException`은 `ErrorCode`와 선택적 details를 운반한다. `ErrorCode` 타입, details 원소 타입이나 둘의 조합을 수동 검사하지 않으며, 최종 공개 응답 구조는 `ErrorResponse` Pydantic schema가 검증한다. 오류별 얇은 exception subclass는 만들지 않는다.
- `responses.py`의 공통 builder는 `ErrorCode`와 details로 `ErrorResponse`를 만들고 HTTP status와 body code를 같은 member의 property에서 생성한다.
- `RequestValidationError` handler는 framework 오류에서 `loc`, `msg`, `type`만 선택해 validation detail로 반환한다. framework 404·405는 각각 canonical `REQUEST_ROUTE_NOT_FOUND`, `REQUEST_METHOD_NOT_ALLOWED` envelope로 변환하고, 잘못된 요청 문자 인코딩은 canonical `INVALID_ARGUMENT`로 변환한다.
- 그 밖에 애플리케이션 계층에서 발생한 문서화되지 않은 `Starlette HTTPException` 4xx·5xx는 계약 위반으로 취급한다. 원본 예외를 내부에 한 번 기록하고, status·detail·header를 외부로 통과시키지 않은 채 canonical `INTERNAL_SERVER_ERROR`를 반환한다. 공개할 의도인 오류는 `ErrorCode`와 `ApiException`으로 명시해야 한다.
- 1xx·2xx·3xx `Starlette HTTPException`은 애플리케이션 오류 계약의 대상이 아니므로 framework 기본 handler에 위임한다.
- 그 밖의 예상하지 못한 예외도 원본을 내부에 한 번 기록하고 공개 응답에는 `INTERNAL_SERVER_ERROR`만 반환한다.
- `ApiException` handler는 canonical `AUTH_INVALID_ACCESS_TOKEN` 오류에만 `WWW-Authenticate: Bearer`를 설정한다. HTTP status가 401이라는 이유만으로 Bearer challenge를 가정하지 않는다. framework 405의 동적 `Allow`는 원래 응답에서 보존한다.
- OAuth callback의 cookie 삭제, `Cache-Control`, `Pragma`와 redirect 조립은 endpoint가 담당한다.
- runtime 응답 조립 모듈(`responses.py`, `exception_handlers.py`)은 OpenAPI adapter를 import하지 않는다. 라우트 선언 모듈은 operation별 OpenAPI response를 `openapi.py` adapter로 투영할 수 있다.
- 응답 전송이 시작된 뒤 발생한 오류, proxy가 자체 생성한 오류, OAuth custom-scheme redirect는 이 애플리케이션 JSON 오류 계약의 범위 밖이다.
- macOS 생성 클라이언트와 그 모델의 갱신·compile·배포는 이번 서버 계약 작업의 범위 밖이다.

## OpenAPI 문서 구성

- `openapi.py`의 `ERROR_DOCS`는 `ErrorCode`를 key로 사용하고 각 오류의 필수 `summary`, `description`과 선택적 validation `example_details`를 소유한다.
- `api_error_responses()`는 `ErrorCode.MEMBER`를 받아 status별 FastAPI `responses` dict로 투영하고 같은 HTTP status의 오류를 하나의 response 아래 병합한다.
- `public_openapi()`는 FastAPI가 자동 생성한 기본 `HTTPValidationError` 422 response와 사용되지 않는 validation schema를 제거한다. 애플리케이션이 명시한 `INVALID_ARGUMENT` 422 response는 유지한다.
- 모든 오류 response는 공통 schema, named examples와 실제 동작에 중요한 header를 문서화한다.
- OpenAPI snapshot은 runtime `app.openapi()`와 구조적으로 같아야 한다.

## 변경 절차

API 계약을 변경할 때는 다음 항목을 한 변경 단위에서 함께 갱신한다.

1. 상태 코드, 응답 본문, 리다이렉트, 헤더와 오류 처리를 포함한 구현
2. 모델·필드·입력·응답을 설명하는 OpenAPI 선언
3. 구현과 OpenAPI 선언의 일치를 검증하는 계약 테스트
4. `openapi/openapi.json` snapshot과 이 문서

구현만 바꾸거나 OpenAPI 선언 또는 계약 테스트만 별도로 바꿔 계약 사이에 불일치가 남지 않도록 한다. 서버 OpenAPI 변경은 macOS 생성 클라이언트에 breaking diff를 만들 수 있으므로 서버 검증과 클라이언트 배포 완료를 구분한다.

## 완료 조건

다음 검수를 모두 마쳐야 API 문서화 작업이 완료된 것으로 본다.

- 자동 테스트로 모델과 필드 설명, 입력 설명, 상태 코드, 응답 스키마, 미디어 타입, 오류 예시와 중요 헤더를 검증한다.
- 생성된 OpenAPI snapshot과 runtime schema가 일치하는지 확인한다.
- Ruff, format, ty, build, 전체 테스트와 diff check의 실제 결과를 기록한다.

Swagger UI 수동 검수는 완료 조건에 포함하지 않는다. 정해진 자동 테스트를 실행하지 않았다면 완료된 것으로 표현하지 않고 `not_run`과 이유를 기록한다. 사용자 검수 전에는 commit하지 않는다.
