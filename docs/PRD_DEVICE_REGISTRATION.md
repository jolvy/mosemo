# Device 등록 API PRD

## Problem Statement

인증된 Mosemo 클라이언트는 활동을 전송하기 전에 계정에 귀속된 서버 식별자를
발급받아야 한다. 현재 Device 모델과 등록 동작이 `activities`에 섞여 있고
`DeviceRegistration`, `deviceRegistrationId`, `device_registrations`처럼 등록 행위가
도메인 이름으로 사용되어, 계정·Device·활동의 책임 경계와 공개 계약이 일치하지
않는다. 네트워크 응답이 유실된 등록 요청을 안전하게 재시도할 계약도 필요하다.

## Solution

서버는 `Device`를 계정에 귀속된 하나의 등록된 앱 설치로 정의하고 독립된 `devices`
경계에서 관리한다. 인증된 클라이언트는 UUID `Idempotency-Key`와 함께
`POST /api/v1/devices`를 호출하며, 서버는 UUIDv7 `deviceId`를 발급한다. 같은 계정과
같은 멱등 키의 재시도는 최초 `deviceId`를 반환한다. 활동 API도 Device를
`deviceId`로만 참조한다.

## User Stories

1. 인증된 사용자로서 앱 설치를 계정의 Device로 등록하고 싶다. 그래야 활동 전송에 사용할 서버 식별자를 얻을 수 있다.
2. 클라이언트로서 서버가 발급한 `deviceId`를 받고 싶다. 그래야 로컬 하드웨어 식별자를 임의로 공개하지 않아도 된다.
3. 클라이언트로서 응답이 유실된 등록 요청을 같은 멱등 키로 재시도하고 싶다. 그래야 중복 Device가 생성되지 않는다.
4. 클라이언트로서 최초 요청과 동일한 재시도에서 같은 성공 상태와 `deviceId`를 받고 싶다. 그래야 네트워크 결과와 무관하게 하나의 등록 흐름을 유지할 수 있다.
5. 클라이언트로서 새로운 등록 시도에는 새로운 멱등 키를 사용하고 싶다. 그래야 서버가 별개의 Device를 발급할 수 있다.
6. 여러 계정을 사용하는 사용자로서 같은 멱등 키가 계정별로 독립적으로 처리되길 원한다. 그래야 한 계정의 등록이 다른 계정과 충돌하지 않는다.
7. 인증되지 않은 호출자로서 Device를 등록할 수 없어야 한다. 그래야 Device 소유권이 항상 유효한 계정에서 시작한다.
8. 클라이언트 개발자로서 멱등 키 누락이나 잘못된 UUID가 공통 검증 오류로 반환되길 원한다. 그래야 기존 오류 처리 계약을 재사용할 수 있다.
9. 활동 전송 클라이언트로서 `deviceId` 하나만 사용하고 싶다. 그래야 등록 전용 용어와 Device 용어를 변환하지 않아도 된다.
10. 활동 데이터 소유자로서 다른 계정의 `deviceId`로 활동을 저장할 수 없어야 한다. 그래야 계정 간 활동 데이터가 섞이지 않는다.
11. 서버 개발자로서 Device 저장과 조회 책임이 `devices`에 모이길 원한다. 그래야 `activities`가 Device 테이블의 규칙을 중복 구현하지 않는다.
12. 서버 개발자로서 공개 API, Python 모델, 데이터베이스가 같은 Device 용어를 사용하길 원한다. 그래야 계층 간 이름 변환과 잘못된 참조를 줄일 수 있다.
13. 운영 전 개발자로서 초기 migration이 최종 Device 스키마를 바로 생성하길 원한다. 그래야 불필요한 호환 migration 없이 깨끗한 초기 스키마를 유지할 수 있다.
14. 제품 개발자로서 앱 재설치로 등록 상태를 잃은 설치가 새 Device로 처리되길 원한다. 그래야 검증되지 않은 물리 하드웨어 연결을 추정하지 않는다.
15. API 소비자로서 이전 `deviceRegistrationId`가 조용히 수용되지 않길 원한다. 그래야 계약 변경 누락을 즉시 발견할 수 있다.

## Implementation Decisions

- 서버에 독립된 `devices` 패키지를 두고 Device 모델, 공개 schema, router, service,
  repository를 소유하게 한다.
- `Device`는 물리 하드웨어가 아니라 계정에 귀속되어 서버에 등록된 앱 설치다.
  등록 상태를 잃은 재설치는 새 Device다.
- 공개 생성 endpoint는 `POST /api/v1/devices`다. Bearer 인증과 UUID 형식의 필수
  `Idempotency-Key` header를 받고 요청 본문은 받지 않는다.
- 최초 생성과 같은 계정·같은 멱등 키의 재시도는 모두 `201 Created`와
  `{ "deviceId": "<UUIDv7>" }`를 반환한다.
- 멱등 키는 계정 범위다. 같은 키라도 계정이 다르면 별개의 Device를 생성하고,
  같은 계정에서 다른 키를 사용하면 새 Device를 생성한다.
- PostgreSQL 원자적 insert와 conflict 무시 후 조회를 결합해 순차 및 동시 재시도가
  하나의 Device로 수렴하게 한다.
- 데이터베이스는 `devices(device_id, account_id, idempotency_key)`를 사용한다.
  `device_id`는 기본 키, `account_id`는 계정 외래 키, `(account_id,
  idempotency_key)`는 유일해야 한다.
- 운영 전 스키마이므로 기존 초기 활동 저장 migration을 직접 수정해 최종 스키마를
  생성한다. 별도의 Device rename 또는 idempotency migration은 유지하지 않는다.
- 활동 레코드는 `device_id` 외래 키를 사용하고 공개 활동 요청은 `deviceId`만 받는다.
  `deviceRegistrationId`와 다른 이전 이름의 호환 alias는 제공하지 않는다.
- `DeviceRepository`는 Device 생성, 멱등 키 조회, 계정 소유권 조회를 담당한다.
  `ActivityRepository`는 활동 저장과 활동 충돌 조회만 담당한다.
- `ActivityService`는 `DeviceRepository`를 통해 요청 Device가 인증 계정 소유인지
  확인한다. 없거나 다른 계정 소유인 Device는 기존 활동 Device 404 계약으로 숨긴다.
- 누락되거나 유효하지 않은 인증은 기존 `AUTH_INVALID_ACCESS_TOKEN` 401 계약을,
  누락되거나 UUID가 아닌 멱등 키와 이전 활동 필드는 기존 `INVALID_ARGUMENT` 422
  계약을 사용한다.
- OpenAPI operation ID와 tag도 `devices` 용어를 사용하며 snapshot과 서버 문서를
  같은 변경에서 갱신한다.
- 구현과 문서 변경은 Mosemo 서버 저장소로 제한한다. macOS 저장소는 변경하지
  않는다.

## Testing Decisions

- 테스트는 구현 클래스의 호출 순서보다 외부에서 관찰 가능한 HTTP 및 저장 결과를
  검증한다.
- 가장 높은 기존 seam인 FastAPI 컴포넌트 테스트에서 Device 생성의 인증, 필수
  header, UUID 검증, 201 응답, camelCase `deviceId`, 빈 요청 본문 계약을 검증한다.
- 같은 컴포넌트 seam에서 활동 요청이 `deviceId`를 수용하고 이전
  `deviceRegistrationId`를 422로 거절하는지 검증한다.
- 실제 PostgreSQL 통합 테스트에서 같은 계정·같은 키의 순차 재시도와 동시 요청이
  동일한 UUIDv7 하나로 수렴하는지 검증한다.
- PostgreSQL 통합 테스트에서 다른 키와 다른 계정의 동일 키가 각각 별개의 Device를
  생성하고, Device 계정 소유권이 활동 저장 전에 강제되는지 검증한다.
- 기존 활동 API 테스트 방식을 따라 없는 Device와 다른 계정 Device가 모두 같은
  공개 404 응답을 반환하는지 검증한다.
- migration은 빈 PostgreSQL에서 전체 upgrade, downgrade, 재upgrade를 실행해 최종
  테이블·외래 키·유일성 제약과 단일 Alembic head를 검증한다.
- OpenAPI 설명 완전성, operation ID 유일성, runtime schema와 snapshot 일치를 기존
  계약 테스트로 검증한다.
- 최종 검증은 Ruff lint·format, 타입 검사, build, 전체 pytest, OpenAPI 생성,
  Git whitespace 검사를 포함한다.

## Out of Scope

- Device 목록, 조회, 이름, 플랫폼, 대표 여부, 우선순위, 활성 상태, 수정, 폐기 및 삭제
- 물리 하드웨어 식별과 재설치 전후 Device 연결
- 한 계정의 단일 활성 Device 강제
- access token 또는 서버 관리 인증 세션과 `deviceId` 연결
- 기존 Device 또는 활동 데이터를 위한 운영 migration과 하위 호환 처리
- `deviceRegistrationId`, 기존 경로 또는 저장 이름을 위한 호환 alias
- macOS 클라이언트, macOS 문서 및 생성 클라이언트 변경
- 활동 저장 의미, 타임라인 계산, batch·stream·offline queue 설계 변경
- 구현 완료 후의 commit 또는 push

## Further Notes

- 이 PRD의 Device 용어는 프로젝트 도메인 glossary와 ADR 0003을 따른다.
- 서버 계약 변경 후 현재 macOS 미커밋 구현은 별도 작업에서 `deviceId`와
  `/api/v1/devices`로 갱신되기 전까지 호환되지 않는다.
- Swagger UI 수동 검수는 자동화된 OpenAPI 계약 검증을 대체하지 않으며 이번 완료
  조건에 포함하지 않는다.
