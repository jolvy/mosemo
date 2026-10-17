---
status: accepted
date: 2026-09-12
---

# 공개 API 오류 경계는 명시적으로 닫고 자동 검증한다

## 맥락

Mosemo 공개 API는 의도한 오류를 `ErrorCode`와 `ApiException`으로 명시하고,
모든 공개 JSON 오류를 하나의 `ErrorResponse` 구조로 반환한다. 런타임 오류
의미와 OpenAPI 표현을 분리하며, 문서화되지 않은 HTTP 오류의 detail과 header는
숨긴다. 요청 검증 detail은 별도 재해석 없이 framework의 `loc`, `msg`, `type`을
공개 계약으로 채택한다.

## 결정

- `ErrorCode` Enum 멤버 이름이 클라이언트가 분기할 `status`의 단일 원천이다.
  각 멤버의 frozen `ErrorSpec` 값은 HTTP `code`와 개발자용 `message`만
  소유한다. 오류별 exception subclass를 만들거나 HTTP 헤더와 OpenAPI
  metadata를 포함하지 않는다.
- 클라이언트는 공통 `ErrorResponse`를 decode한 뒤 `error.status`로 분기한다.
  같은 HTTP status의 오류는 하나의 schema와 status별 named example로
  문서화하며, 오류별 `oneOf`나 discriminator를 만들지 않는다.
- `ERROR_DOCS`는 `ErrorCode`를 key로 사용해 summary, description, example
  같은 OpenAPI 전용 정보를 소유한다. 런타임 응답 모듈은 OpenAPI 모듈을
  import하지 않으며, 두 registry의 완전한 대응은 자동 계약 테스트로
  검증한다.
- HTTP 헤더는 전송 문맥이 소유한다. 인증 challenge는 exception handler가,
  동적 `Allow`는 framework 405 응답이, OAuth cookie·cache·redirect 헤더는
  endpoint가 담당하고 OpenAPI는 이를 별도로 설명한다.
- canonical 처리 대상이 아닌 애플리케이션 계층의 4xx·5xx
  `Starlette HTTPException`은 계약 위반으로 취급한다. 원본을 한 번 기록한 뒤
  status, detail, header를 숨기고 `INTERNAL_SERVER_ERROR`로 반환한다.
- validation detail은 `RequestValidationError.errors()`의 순서와 `loc`, `msg`,
  `type`을 그대로 따른다. union branch, 모델명, 배열 index와 JSON parser
  offset을 재해석하거나 제거하지 않고, `input`, `ctx`, `url`만 제외한다.
  Pydantic 이전의 인코딩 실패는 같은 구조의 서버 정의 `invalid_encoding`
  detail을 사용한다.
- 구형 오류 envelope, alias 또는 dual serialization을 제공하지 않는다.
- 성공·오류 응답, 중요 헤더, runtime OpenAPI와 snapshot의 일치는 반복 가능한
  자동 계약 테스트로 검증한다. 이 자동 테스트만 완료 조건으로 사용하며
  Swagger UI 수동 검수는 완료 조건에 포함하지 않는다.

### 오류 경계의 적용 세부사항

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

### 검증 detail의 적용 세부사항

`RequestValidationError`를 처리할 때 `errors()`의 순서와 각 오류의 `loc`, `msg`, `type`을 그대로 사용한다. 공개 detail에는 이 세 필드만 포함한다.

- `loc`은 Pydantic validation error의 location tuple 원문이다. JSON에서는 문자열과 정수 segment의 배열로 직렬화하며 `body`, `query`, `path`, `header`, `cookie`, 배열 index, union branch와 모델명 segment를 제거하거나 변환하지 않는다.
- `msg`는 Pydantic validation error의 사용자용 message 원문이다. custom validator message도 그대로 공개될 수 있으므로 validator message에 비밀값이나 내부 구현 정보를 넣지 않는다.
- `type`은 Pydantic validation error의 type 원문이다. 대소문자나 의미를 변환하지 않으며 OpenAPI에서 닫힌 enum으로 표현하지 않는다.
- Pydantic의 `input`, `ctx`, `url`과 그 밖의 오류 metadata는 공개하지 않는다.
- JSON 문법 오류도 framework가 제공한 `loc`, `msg`, `type`을 그대로 사용하므로 parser offset이 `loc`의 정수 segment로 포함될 수 있다.
- Pydantic에 도달하기 전 발생한 잘못된 요청 문자 인코딩은 같은 detail 구조로 `loc: ["body"]`, `msg: "Invalid request body encoding"`, `type: "invalid_encoding"`을 반환한다.
- `loc`, `msg`, `type` 중 하나가 없거나 오류 목록이 비어 있으면 값을 임의로 보완하지 않고 예기치 않은 내부 오류로 처리한다.
- 이전 `location`, `field`, `reason`, `fieldValue` 형식은 제공하지 않는다.

### OpenAPI adapter의 책임

- `openapi.py`의 `ERROR_DOCS`는 `ErrorCode`를 key로 사용하고 각 오류의 필수 `summary`, `description`과 선택적 validation `example_details`를 소유한다.
- `api_error_responses()`는 `ErrorCode.MEMBER`를 받아 status별 FastAPI `responses` dict로 투영하고 같은 HTTP status의 오류를 하나의 response 아래 병합한다.
- `public_openapi()`는 FastAPI가 자동 생성한 기본 `HTTPValidationError` 422 response와 사용되지 않는 validation schema를 제거한다. 애플리케이션이 명시한 `INVALID_ARGUMENT` 422 response는 유지한다.
- 모든 오류 response는 공통 schema, named examples와 실제 동작에 중요한 header를 문서화한다.
- OpenAPI snapshot은 runtime `app.openapi()`와 구조적으로 같아야 한다.

## 고려한 대안

- 오류별 exception subclass와 `oneOf`/discriminator는 같은 형태의 payload를
  불필요하게 여러 타입으로 만들고 런타임 예외 구조를 문서 표현에 결합하므로
  선택하지 않았다.
- `ErrorSpec`에 헤더와 OpenAPI metadata를 함께 두는 방식은 payload 의미,
  HTTP 전송, 문서화 책임을 섞으므로 선택하지 않았다.
- 등록되지 않은 `HTTPException`을 그대로 전달하는 방식은 framework 고유
  payload와 내부 detail·header를 공개 계약으로 만들 수 있어 선택하지 않았다.
- `loc`을 공개 field 경로로 다시 조립하는 방식은 union branch와 실제 필드를
  일반적으로 구분할 수 없고 FastAPI/Pydantic 표현을 휴리스틱으로 추론해야 하므로
  선택하지 않았다.
- Swagger UI 수동 검수를 완료 조건으로 두는 방식은 반복 가능하지 않으므로
  선택하지 않았다.

## 결과와 트레이드오프

- 공통 schema만으로 `status`, `code`, `message` 조합을 정적으로 제한하지는
  못한다. canonical 값과 OpenAPI 문서의 일치는 계약 테스트가 보장한다.
- 새 보안·rate-limit·외부 라이브러리가 401·403·429 등을 `HTTPException`으로
  발생시키면 명시적인 `ErrorCode`와 handler 연결 전까지 외부에는 500으로
  보인다.
- Pydantic/FastAPI 버전 변경으로 validation `loc`, `msg`, `type`이 바뀔 수
  있으므로 클라이언트는 이를 Mosemo가 보장하는 안정적인 분기 코드로 사용하지
  않는다.
- Pydantic과 custom validator의 `msg`를 그대로 공개하므로 validator message에
  비밀값이나 내부 구현 정보를 넣지 않아야 한다.
- 서버 오류 계약과 생성 클라이언트는 별도 작업으로 갱신할 수 있지만, 배포할
  때는 서로 호환되는 버전을 함께 조정해야 한다.

## 대체한 ADR

없음. 이 ADR이 다른 ADR을 대체한다는 기록은 원문에 없다.

## 연관 PRD·ADR

- [공개 JSON·시간·응답 계약 ADR](0009-define-public-json-timestamps-and-responses.md)
- [Device 등록 PRD](../prd/device-registration.md)
- [관찰 타임라인 PRD](../prd/observation-timeline.md)

2026-10-14에는 문서 형식과 링크만 정비했다. 원래 결정·대안·결과와 결정 날짜를 보존하며, 과거의 개발 단계 예외를 새로 승인하지 않는다.
