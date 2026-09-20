# 활동 저장 ERD

> 상태: 활동 원본 저장과 관찰 타임라인 projection 구현
> 범위: 계정 시간대, 활동 원본, 저장형 관찰 타임라인

> 계약: ADR 0004와 `docs/PRD_OBSERVATION_TIMELINE.md`를 따른다.

인증용 `native_auth_codes`는 활동 저장 범위가 아니므로 표시하지 않는다.
아래 테이블은 SQLAlchemy 모델과 migration으로 구현되어 있다. 초기 타임라인
원본 데이터가 없으므로 projection의 기존 기록 backfill은 수행하지 않는다.

```mermaid
erDiagram
    accounts ||--o{ devices : owns
    devices ||--o{ activity_records : produces
    accounts ||--o{ activity_timeline_segments : projects
    activity_records ||--o{ activity_timeline_segments : anchors

    accounts {
        uuid account_id PK
        varchar provider
        varchar provider_subject
        varchar timezone
        timestamptz created_at
        timestamptz last_authenticated_at
    }

    devices {
        uuid device_id PK
        uuid account_id FK
        uuid idempotency_key
    }

    activity_records {
        uuid event_id PK
        uuid device_id FK
        bigint sequence
        varchar record_type
        timestamptz observed_at
        text timezone_id
        integer utc_offset_minutes
        jsonb payload
        timestamptz received_at
    }

    activity_timeline_segments {
        uuid segment_id PK
        uuid account_id FK
        varchar segment_type
        timestamptz started_at
        timestamptz ended_at
        uuid first_event_id FK
        uuid last_event_id FK
        timestamptz last_observed_at
        jsonb context
        text reason
    }
```

## `accounts`

날짜 조회는 계정의 IANA 시간대로 현지 날짜 경계를 계산한다. 개발 단계의 명시적
예외로 첫 계정 생성 migration에 시간대 컬럼을 추가했다.

| 컬럼 | 타입 | 제약 | 의미 |
| --- | --- | --- | --- |
| `account_id` | UUID | PK | 계정 식별자. |
| `provider` | VARCHAR(32) | NOT NULL | 계정 공급자. 현재 `KAKAO`만 사용한다. |
| `provider_subject` | VARCHAR(255) | NOT NULL | 공급자가 부여한 사용자 식별자. |
| `timezone` | VARCHAR(255) | NOT NULL, `Asia/Seoul` DB default | 날짜 조회의 계정 시간대. Python `Timezone` enum으로 다루며 DB CHECK는 없다. |
| `created_at` | TIMESTAMPTZ | NOT NULL, server default | 계정 생성 시각. |
| `last_authenticated_at` | TIMESTAMPTZ | NOT NULL, server default | 마지막 인증 시각. |

추가 제약:

- `UNIQUE(provider, provider_subject)`

## `devices`

한 계정에 귀속되어 서버에 등록된 앱 설치다. 한 계정은 여러 Device를 가질 수 있지만
동시에 하나만 접속하도록 강제하는 인증 상태는 이 테이블에 저장하지 않는다.

| 컬럼 | 타입 | 제약 | 의미 |
| --- | --- | --- | --- |
| `device_id` | UUID | PK | 서버가 발급한 Device 식별자. |
| `account_id` | UUID | NOT NULL, FK | Device를 소유한 계정. |
| `idempotency_key` | UUID | NOT NULL | 클라이언트가 하나의 등록 시도에 부여한 멱등 키. |

외래 키와 인덱스:

- `account_id -> accounts.account_id ON DELETE CASCADE`
- `INDEX(account_id)`
- `UNIQUE(account_id, idempotency_key)`

`POST /api/v1/devices`는 서버가 `device_id`를 발급한다.
클라이언트는 요청 전에 UUID `Idempotency-Key`를 저장하고, 응답을 받기 전 재시도에는
같은 값을 사용한다. 같은 계정과 같은 키에는 최초 발급한 식별자를 반환한다.

기기 이름, 플랫폼, 대표 여부, 우선순위, 활성 여부, 폐기 시각은 초기 저장에 필요하지
않으므로 두지 않는다.

## `activity_records`

개인정보 필터 후 활동 레코드의 source of truth다. 원본 삽입과 같은 트랜잭션에서
관찰 구간 projection을 갱신하지만, GET이 원본에서 다시 grouping하지 않는다.

| 컬럼 | 타입 | 제약 | 의미 |
| --- | --- | --- | --- |
| `event_id` | UUID | PK | 클라이언트가 생성한 레코드 식별자이자 단건 재시도 멱등성 키. |
| `device_id` | UUID | NOT NULL, FK | 레코드를 생성한 Device. |
| `sequence` | BIGINT | NOT NULL | 같은 Device 안의 증가 순번. |
| `record_type` | VARCHAR(32) | NOT NULL, CHECK | 레코드 종류. |
| `observed_at` | TIMESTAMPTZ | NOT NULL | 클라이언트가 관찰에 부여한 UTC 시각. |
| `timezone_id` | TEXT | NOT NULL | 관찰 당시 시간대 식별자. Python `Timezone` enum으로 다루며 DB CHECK는 없다. |
| `utc_offset_minutes` | INTEGER | NOT NULL | 관찰 당시 UTC와 현지 시간의 차이(분). |
| `payload` | JSONB | NOT NULL | 레코드 종류별 본문. |
| `received_at` | TIMESTAMPTZ | NOT NULL, server default | 서버가 레코드를 수신해 저장한 시각. |

제약:

- `UNIQUE(device_id, sequence)`
- `CHECK(sequence >= 0)`
- `CHECK(record_type IN ('activity_observation', 'collection_state_changed'))`
- `device_id -> devices.device_id ON DELETE CASCADE`

인덱스:

- PK와 UNIQUE 제약이 `event_id`, `(device_id, sequence)` 조회를 지원한다.
- Device별 기간 조회를 위해 `INDEX(device_id, observed_at)`를 둔다.

### JSONB payload

`payload`에는 이미 Pydantic 검증을 통과한 종류별 본문만 저장한다.

| `record_type` | payload 구조 |
| --- | --- |
| `activity_observation` | `{ "context": DetailedActivityContext \| OpaqueActivityContext }` |
| `collection_state_changed` | `{ "state": "active" \| "suspended", "reason": string }` |

공통 envelope를 JSONB에 다시 복제하지 않는다. 같은 `event_id`가 다시 들어오면
서버가 생성한 `received_at`을 제외한 일반 컬럼과 payload를 직접 비교하며,
`event_hash`는 두지 않는다.

## `activity_timeline_segments`

계정 전체의 모든 Device에서 관찰한 동일 문맥의 활동 구간과 명시적 수집 공백을
함께 보관하는 재구축 가능한 읽기 모델이다. Device 출처는 grouping·응답 경계가
아니다. `segment_id`는 단순 연장·종료 때 유지되지만 영구 도메인 ID는 아니다.

| 컬럼 | 타입 | 제약 | 의미 |
| --- | --- | --- | --- |
| `segment_id` | UUIDv7 | PK | 저장된 구간의 projection 식별자. |
| `account_id` | UUID | NOT NULL, FK | 구간을 소유한 계정. |
| `segment_type` | VARCHAR(32) | NOT NULL, CHECK | `activity` 또는 `capture_gap`. |
| `started_at` | TIMESTAMPTZ | NOT NULL | 첫 원본 이벤트의 관찰 시각. |
| `ended_at` | TIMESTAMPTZ | nullable, CHECK | 실제 관찰 또는 침묵에 근거한 종료 시각. 0초도 허용한다. |
| `first_event_id` | UUID | NOT NULL, FK | 구간을 시작한 원본 이벤트. |
| `last_event_id` | UUID | activity에 필수, gap에서는 NULL | 마지막 동일 문맥 활동 관찰 이벤트. |
| `last_observed_at` | TIMESTAMPTZ | activity에 필수, gap에서는 NULL | 활동의 마지막 실제 관찰 시각. |
| `context` | JSONB | activity에 필수, gap에서는 NULL | 개인정보 필터를 마친 원본 전체 문맥. |
| `reason` | TEXT | gap에 필수, activity에서는 NULL | 첫 `suspended`의 이유. |

`account_id`는 계정 삭제에 cascade한다. 두 이벤트 FK는 개별 원본 삭제 전에
projection을 수정하도록 deferred `NO ACTION`이다. 종류별 필수·NULL, 종료 시각
순서, 마지막 관찰 시각 순서는 DB CHECK로 검증한다. 계정과 시작 시각 인덱스가
날짜 조회와 재계산 경계를 지원한다.

## 저장하지 않는 모델

초기 ERD에는 다음 테이블과 컬럼을 포함하지 않는다.

- `activity_ingest_batches`, `batch_id`, `request_hash`
- `activity_collection_streams`, `collection_stream_id`, stream watermark
- 별도 활동 구간·수집 공백 테이블, projection version/state table
- `clock_epoch_id`, `monotonic_ns`
- `event_hash`
- 대표 기기, 기기 우선순위, 활동 자동 병합 상태
- AI 해석과 사용자 확정 의미

## 조회 경계

계정 타임라인 GET은 계정 소유의 `activity_timeline_segments`를 날짜와 겹침
조건으로 선택한다. 구간 순서 동률은 `first_event_id`가 가리키는 원본의
`received_at`, `event_id`로 정한다. 원본이나 Device 식별자는 응답에 노출하지
않고, 날짜 경계에서 구간을 자르지 않는다.

초기 제품은 계정마다 동시에 하나의 기기만 접속한다고 가정한다. 이 가정은 현재
ERD만으로 강제되지 않으며, 새 로그인 시 기존 JWT를 즉시 무효화하는 인증 TODO가
완료되어야 서버 불변조건이 된다.
