---
status: accepted
date: 2026-11-04
---
# 오류 코드는 BC가 소유하고, 공개 식별자와 오류 코드 이름을 BC 경계에 맞춘다

## 맥락

[ADR 0001](0001-public-api-error-boundary.md)은 `exceptions.py`의 `ErrorCode` enum 하나를
클라이언트가 분기할 `status`의 단일 원천으로 정했다. 런타임 정의(`ErrorSpec`: HTTP `code`,
`message`)와 OpenAPI 설명(`openapi.py`의 `ERROR_DOCS`)을 분리하고, HTTP 헤더는 전송 문맥이
소유하게 했다.

[ADR 0011](0011-split-bounded-contexts-logically-with-layered-packages.md)로 BC를 나누면 이 중앙
enum과 `ERROR_DOCS`가 모든 BC의 오류를 아는 결합점이 된다. exception handler도
`AUTH_INVALID_ACCESS_TOKEN`, `ACTIVITY_TIMELINE_BUSY`, `ACTIVITY_DELIVERY_UNAVAILABLE` 같은 특정
BC의 코드로 헤더를 분기한다.

공개 이름도 BC 경계와 어긋난다.

- 라벨 확정이 쓰는 오류 4개가 `ACTIVITY_` 접두사를 쓴다(`ACTIVITY_SEGMENT_NOT_FOUND`,
  `ACTIVITY_SEGMENT_NOT_LABELABLE`, `ACTIVITY_SEGMENT_CHANGED`,
  `ACTIVITY_LABEL_CONFIRMATION_CONFLICT`).
- 리소스 자신의 식별자 이름이 응답마다 다르다(`accountId`, `deviceId`, `sessionId`, `labelId`,
  `segmentId`). [ADR 0013](0013-persist-dataclass-aggregates-with-imperative-mapping.md)은 내부
  식별자를 `id`, 참조를 `{엔티티}_id`로 정했다.

macOS 클라이언트는 라벨 확정 거절 사유를 `status` 문자열로 분기하고
(`Sources/MosemoAPI/LiveMosemoAPIClient.swift`), 생성된 OpenAPI 타입의 식별자 필드를 쓴다.
서버를 운영하지 않는 기간이라 호환 기간 없이 서버와 클라이언트를 함께 바꿀 수 있다.

선택 기준은 다음과 같다.

- 각 BC가 자기 오류와 그 문서를 소유하고, 공통 장치는 특정 BC를 모르는가
- ADR 0001의 런타임·OpenAPI 분리와 헤더 소유 원칙을 유지하는가
- `status` 문자열이 BC를 넘어 중복되지 않음을 검증하는가
- 공개 이름만 보고 소유 BC와 식별자의 의미를 알 수 있는가
- 클라이언트 변경 범위가 한 번에 끝나는가

## 결정

### 오류 코드의 소유

```text
shared/errors.py                  ErrorSpec(code, message), ApiErrorCode(멤버 없는 Enum 기반), CommonErrorCode, ApiException
shared/openapi.py                 api_error_responses(*codes): 오류 코드를 HTTP status별로 묶어 선언만 함(설명 없음)
{bc}/presentation/errors.py       {Bc}ErrorCode(ApiErrorCode): code와 message만. public.py에서 다시 내보냄
{bc}/presentation/error_docs.py   ERROR_DOCS: {Bc}ErrorCode → OpenAPI summary, description, 예시. bootstrap.py가 제공
api/exception_handlers.py         헤더 정책
api/openapi.py                    public_openapi()가 모든 BC의 ERROR_DOCS를 합쳐 선언된 오류 응답에 설명과 예시를 채움
```

- 라우터는 자기 BC와 다른 BC(`public.py`로 가져온)의 오류 코드를 `api_error_responses`로
  선언만 한다. 라우터를 선언하는 시점에는 다른 BC의 문서를 알 필요가 없다. 설명과 예시는 조립
  루트가 OpenAPI를 만들 때 각 BC의 `bootstrap.py`가 제공한 `ERROR_DOCS`를 합쳐 채운다. ADR
  0001에서 이미 `public_openapi()`가 생성된 문서를 후처리하는 자리다.

- ADR 0001의 원칙은 그대로 둔다. `ErrorSpec`은 `code`와 `message`만 가지며, OpenAPI 설명은 별도
  레지스트리에, 헤더는 전송 문맥에 둔다. 런타임 응답 모듈은 OpenAPI 모듈을 import하지 않는다.
- `ApiException`과 응답 builder는 특정 enum이 아니라 `ApiErrorCode`를 받는다. `status`는 enum
  멤버 이름이다.
- `CommonErrorCode`에는 여러 BC와 프레임워크가 함께 쓰는 코드만 둔다.
  `REQUEST_ROUTE_NOT_FOUND`, `REQUEST_METHOD_NOT_ALLOWED`, `INVALID_ARGUMENT`,
  `INTERNAL_SERVER_ERROR`, `AUTH_INVALID_ACCESS_TOKEN`이다. 인증 실패는 모든 BC 라우터가 쓰므로
  identity가 아니라 공통에 둔다.
- 다른 BC의 오류 코드가 필요하면 그 BC의 `public.py`로 가져온다. 예를 들어 집중 세션 완료는
  labels의 `LABEL_NOT_AVAILABLE`을 쓰고, 라벨 확정은 observation의 `ACTIVITY_TIMELINE_BUSY`를 쓴다.
- 헤더 정책은 조립 루트의 exception handler가 가진다. `WWW-Authenticate`는
  `CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN`에, `Retry-After`는 observation `public.py`의
  `ACTIVITY_TIMELINE_BUSY`와 `ACTIVITY_DELIVERY_UNAVAILABLE`에 붙인다.

| BC | enum | 멤버 |
| --- | --- | --- |
| identity | `IdentityErrorCode` | `AUTH_INVALID_AUTHORIZATION_CODE`, `AUTH_INVALID_OAUTH_CONTEXT` |
| labels | `LabelErrorCode` | `LABEL_NOT_AVAILABLE` |
| observation | `ObservationErrorCode` | `ACTIVITY_DEVICE_NOT_FOUND`, `ACTIVITY_EVENT_ID_CONFLICT`, `ACTIVITY_SEQUENCE_CONFLICT`, `ACTIVITY_BATCH_CONFLICT`, `ACTIVITY_BATCH_TOO_LARGE`, `ACTIVITY_DELIVERY_UNAVAILABLE`, `ACTIVITY_TIMELINE_BUSY`, `FOCUS_SESSION_NOT_FOUND`, `FOCUS_SESSION_CONFLICT` |
| labeling | `LabelingErrorCode` | `LABELING_SEGMENT_NOT_FOUND`, `LABELING_SEGMENT_NOT_LABELABLE`, `LABELING_SEGMENT_CHANGED`, `LABELING_CONFIRMATION_CONFLICT` |

### 오류 코드 이름

- 접두사는 그 코드를 소유한 BC의 개념을 드러내며, 다른 BC의 접두사를 쓰지 않는다.

  | enum | 허용 접두사 |
  | --- | --- |
  | `CommonErrorCode` | `REQUEST_`, `INVALID_`, `INTERNAL_`, `AUTH_INVALID_ACCESS_TOKEN`(이 멤버 하나) |
  | `IdentityErrorCode` | `AUTH_` |
  | `LabelErrorCode` | `LABEL_` |
  | `ObservationErrorCode` | `ACTIVITY_`, `FOCUS_SESSION_` |
  | `LabelingErrorCode` | `LABELING_` |
- labeling의 오류 4개 이름을 바꾼다.

  | 현재 | 변경 |
  | --- | --- |
  | `ACTIVITY_SEGMENT_NOT_FOUND` | `LABELING_SEGMENT_NOT_FOUND` |
  | `ACTIVITY_SEGMENT_NOT_LABELABLE` | `LABELING_SEGMENT_NOT_LABELABLE` |
  | `ACTIVITY_SEGMENT_CHANGED` | `LABELING_SEGMENT_CHANGED` |
  | `ACTIVITY_LABEL_CONFIRMATION_CONFLICT` | `LABELING_CONFIRMATION_CONFLICT` |

- 일괄 확정 실패 detail의 `type`은 `status`의 소문자이므로 함께 바뀐다.
- 구형 이름의 alias는 두지 않는다(ADR 0001).

### 공개 식별자 이름

- 리소스 자신을 나타내는 응답과 요청에서 자기 식별자는 `id`로 한다. 다른 리소스를 가리킬 때는
  `{리소스}Id` 또는 중첩 객체(`account: { id }`)를 쓰고, 어느 쪽인지는 응답마다 판단한다.
- 이번에 바뀌는 필드는 다음과 같다.

  | 스키마 | 현재 | 변경 |
  | --- | --- | --- |
  | `AccountResponse` | `accountId` | `id` |
  | `DeviceCreateResponse` | `deviceId` | `id` |
  | `FocusSessionCreateRequest`, `FocusSessionResponse` | `sessionId` | `id` |
  | `LabelResponse` | `labelId` | `id` |
  | `ActivitySegmentResponse`, `CaptureGapResponse`, `InProgressActivityResponse`, `OpaqueActivityResponse`, `LabelTimelineSegmentResponse`, `LabelTimelineCaptureGapResponse` | `segmentId` | `id` |
  | 경로 `/api/v1/focus-sessions/{session_id}/completion` | `session_id` | `focus_session_id` |

- 참조는 바꾸지 않는다. `FocusSessionResponse.deviceId`·`labelId`,
  `BatchActivityObservation.focusSessionId`, `LabelSelectionRequest.labelId`, 확정 요청 항목과
  라벨 상태 응답의 `segmentId`, 경로 `/segments/{segment_id}`가 해당한다.
- 클라이언트가 만든 식별자인 `eventId`와 `batchId`는 예외로 두고 바꾸지 않는다.

### 적용 순서

- 오류 코드 소유 이동은 공개 계약을 바꾸지 않으므로 BC 재구성 작업에서 한다.
- 오류 코드 이름 변경과 공개 식별자 변경은 공개 계약 변경이다. 서버 작업과 macOS 작업을 짝으로
  만들어 함께 반영한다. macOS 쪽은 OpenAPI로 코드를 다시 생성하고, `status` 분기와 테스트 고정값,
  식별자 필드 사용처를 고친다.

### 강제 방법

| 규칙 | 도구 |
| --- | --- |
| 모든 BC의 `status` 문자열이 서로 겹치지 않음 | 조립 루트 단위 테스트 |
| 각 enum의 멤버 접두사가 그 BC의 허용 접두사에 속함 | 조립 루트 단위 테스트 |
| BC별 enum과 `ERROR_DOCS`가 정확히 대응 | 계약 테스트(기존 대응 테스트를 BC 합본 기준으로) |
| 런타임 OpenAPI와 `openapi/openapi.json` 일치 | 기존 계약 테스트 |
| 응답 builder·handler가 OpenAPI 모듈을 import하지 않음 | import-linter `forbidden` |

공개 식별자 이름 원칙은 기계적으로 검사하지 않는다. 리뷰와 `openapi/openapi.json` 스냅샷
diff로 확인한다.

## 고려한 대안

| 대안 | 장점 | 단점·비용 |
| --- | --- | --- |
| 중앙 `ErrorCode` 유지 | 중복 검사가 enum 하나로 끝나고 변경이 없다. | 모든 BC의 오류를 아는 결합점이 `shared`에 남고, handler가 특정 BC 코드를 직접 안다. |
| BC별 enum에 OpenAPI 설명과 헤더를 `ErrorSpec`으로 함께 둠 | 오류 하나의 정보가 한곳에 모인다. | ADR 0001의 런타임·OpenAPI 분리와 헤더의 전송 문맥 소유 원칙을 뒤집는다. |
| 이름은 그대로 두고 소유만 이동 | 클라이언트 변경이 없다. | labeling 오류가 observation 접두사를 계속 쓰고, 식별자 이름이 응답마다 다르게 남는다. |
| 구형 이름 alias를 함께 제공 | 클라이언트를 천천히 바꿀 수 있다. | ADR 0001이 alias와 이중 직렬화를 금지했고, 서버를 운영하지 않아 호환 기간이 필요 없다. |
| 공개 식별자 규칙을 기반 응답 클래스나 OpenAPI 린터로 강제 | 위반을 자동으로 잡는다. | "자기 리소스"를 이름으로 추측하면 오판하고, 표시 클래스나 Node 기반 린터를 새로 유지해야 한다. 얻는 것이 작다. |

## 결과와 트레이드오프

- 각 BC가 자기 오류와 문서를 소유하고, 공통 장치는 `ApiErrorCode`만 안다.
- 중앙 enum이 하던 중복 방지는 조립 루트 테스트가 맡는다.
- 헤더 정책이 조립 루트에 남으므로, 새 BC가 헤더가 필요한 오류를 추가하면 조립 루트의 handler도
  바뀐다.
- macOS 클라이언트가 함께 바뀌어야 한다. 서버만 먼저 반영하면 라벨 확정 거절 사유가 일반 오류로
  떨어지고, 생성 타입의 식별자 필드가 맞지 않는다.

## 대체한 ADR

- [ADR 0001](0001-public-api-error-boundary.md)을 부분 대체한다. 바뀌는 조항은 다음과 같다.
  - `exceptions.py`의 `ErrorCode` Enum 하나가 공개 오류 정의와 `status`를 소유한다 → BC별
    enum과 `CommonErrorCode`가 소유하고, 공통 기반은 `ApiErrorCode`다.
  - `ApiException`과 `responses.py`의 builder가 `ErrorCode`를 받는다 → `ApiErrorCode`를 받는다.
  - `openapi.py`의 `ERROR_DOCS`가 모든 오류의 문서를 소유한다 → BC별 `ERROR_DOCS`를 조립 루트가
    합친다.
  - `api_error_responses()`가 `ErrorCode.MEMBER`를 받아 문서까지 투영한다 → `ApiErrorCode`를
    받아 응답을 선언하고, 설명과 예시는 `public_openapi()`가 채운다.
  - `ApiException` handler가 특정 코드로 헤더를 분기한다는 원칙은 유지하되, 대상 코드를 각 BC의
    `public.py`에서 가져온다.

  런타임과 OpenAPI의 분리, 헤더의 전송 문맥 소유, 공통 오류 구조, validation detail 규칙, alias
  금지는 ADR 0001을 그대로 따른다.

## 연관 PRD·ADR

- [ADR 0001](0001-public-api-error-boundary.md)
- [ADR 0009](0009-define-public-json-timestamps-and-responses.md): 공개 JSON 필드는 camelCase를
  유지한다.
- [ADR 0011](0011-split-bounded-contexts-logically-with-layered-packages.md)
- [ADR 0013](0013-persist-dataclass-aggregates-with-imperative-mapping.md)
