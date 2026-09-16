---
status: accepted
date: 2026-09-12
---

# 공개 API 오류 경계는 명시적으로 닫고 자동 검증한다

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

## 결과

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
