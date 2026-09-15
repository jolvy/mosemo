# macOS 활동 수집 설계

> 상태: 초기 활동 원본 저장 구현 완료
> 범위: macOS 클라이언트의 관찰, 활동 레코드 단건 수신, 원본 저장, 조회 시 타임라인 계산
> 비범위: 배치 전송, 오프라인 수집, 저장형 타임라인 projection, AI 해석, 사용자 확정 의미, 통계·집중도

현재 `src/mosemo/activities/schemas.py`는 이 문서의 단건 공개 요청 계약을 구현한다.
기존
`docs/REQUIERMENTS.md`의 batch·오프라인 관련 문구는 이번 작업 범위에서 수정하지
않았으므로 별도로 정합성을 맞춰야 한다.

## 1. 목표와 책임 경계

초기 버전은 개인정보 필터를 통과한 활동 사실을 레코드 하나씩 서버에 저장하고,
필요할 때 원본을 읽어 활동 타임라인을 계산한다.

```text
macOS 클라이언트                  서버                         화면
관찰·필터·단건 전송       ->  원본 검증·저장          ->  조회 시 타임라인 계산
```

| 주체 | 책임 |
| --- | --- |
| 클라이언트 | 포커싱된 대상만 관찰하고, 개인정보를 필터링한 전체 스냅샷을 한 레코드씩 전송한다. |
| 서버 | 레코드 식별자와 기기별 순서를 검증하고 원본을 저장한다. 활동 구간과 수집 공백은 조회할 때 계산한다. |
| 화면 | 서버가 원본에서 계산한 타임라인을 표시한다. |

클라이언트는 활동 구간을 만들거나 서로 다른 관찰을 하나의 의미로 합치지 않는다.
서버도 초기 버전에서는 계산 결과를 별도 테이블에 저장하지 않는다.

### 핵심 용어

| 용어 | 의미 |
| --- | --- |
| Device | 한 계정에 귀속되어 서버에 등록된 앱 설치. 재설치로 등록 상태를 잃으면 새 Device가 된다. |
| 관찰 | 클라이언트가 한 시점에 포커싱된 대상을 읽은 사실. |
| 전체 스냅샷 | 이전 값과의 차이가 아니라 해당 시점에 알 수 있는 전체 관찰값. |
| 활동 레코드 | 서버가 원본으로 보관하는 하나의 관찰 또는 수집 상태 변경. |
| 활동 구간 | 연속된 관찰을 비교해 조회 시 계산하는 표시 단위. |
| 수집 공백 | 실제로 관찰할 수 없었던 기간. 불투명 활동과 다르다. |
| 불투명 활동 | 관찰은 했지만 식별 정보를 저장하지 않기로 한 정상 활동. |

## 2. 전송 계약

### 2.1 단건 전송

요청 하나에는 한 기기의 활동 레코드 하나만 담는다.

```json
{
  "deviceId": "30000000-0000-0000-0000-000000000000",
  "eventId": "40000000-0000-0000-0000-000000000000",
  "sequence": 412,
  "recordType": "activity_observation",
  "observedAt": "2026-09-13T10:15:30Z",
  "timezoneId": "Asia/Seoul",
  "utcOffsetMinutes": 540,
  "context": {
    "kind": "opaque"
  }
}
```

| 필드 | 의미 |
| --- | --- |
| `deviceId` | 인증된 계정에 귀속된 Device 식별자. |
| `eventId` | 레코드 재시도와 중복 제거에 사용하는 클라이언트 생성 식별자. |
| `sequence` | 같은 Device 안에서 증가하는 레코드 순번. |
| `recordType` | `activity_observation` 또는 `collection_state_changed`. |
| `observedAt` | 클라이언트가 관찰에 부여한 UTC 시각. |
| `timezoneId`, `utcOffsetMinutes` | 관찰 당시의 현지 시간대 문맥. |

`batchId`, `collectionStreamId`, `clockEpochId`, `monotonicNs`는 초기 공개 요청에
포함하지 않으며, 대응하는 서버 저장 컬럼도 두지 않는다. 클라이언트는 온라인 시간
동기화로 얻은 UTC 기준점과 로컬 단조 시계의 경과시간을 이용해 `observedAt`을 만들 수 있지만,
서버는 그 내부 계산 근거를 저장하지 않는다.

### 2.2 활동 관찰

`activity_observation`은 항상 전체 스냅샷을 보낸다.

```json
{
  "deviceId": "30000000-0000-0000-0000-000000000000",
  "recordType": "activity_observation",
  "eventId": "40000000-0000-0000-0000-000000000000",
  "sequence": 412,
  "observedAt": "2026-09-13T10:15:30Z",
  "timezoneId": "Asia/Seoul",
  "utcOffsetMinutes": 540,
  "context": {
    "kind": "detailed",
    "app": {
      "bundleId": { "status": "captured", "value": "com.apple.Safari" },
      "name": { "status": "captured", "value": "Safari" }
    },
    "window": {
      "status": "captured",
      "title": {
        "status": "captured",
        "value": "프로젝트 설계 문서",
        "truncated": false
      }
    },
    "web": {
      "kind": "browser",
      "tabTitle": {
        "status": "captured",
        "value": "Mosemo",
        "truncated": false
      },
      "url": {
        "status": "captured",
        "value": "https://example.com/work"
      }
    }
  }
}
```

앱·창·웹 필드는 독립적으로 상태를 표현한다. 한 필드를 읽지 못했다고 다른 필드까지
없는 것으로 취급하지 않는다.

| 영역 | 구조와 의미 |
| --- | --- |
| `app` | `bundleId`와 표시 이름을 각각 수집한다. |
| `window` | `captured`, `absent`, `unavailable` 중 하나다. 창 제목은 길이 제한을 넘으면 앞부분과 원래 바이트 길이를 기록한다. |
| `web` | `not_applicable` 또는 `browser`다. 브라우저일 때 탭 제목과 URL은 각각 독립 상태다. |
| 탭 제목·URL | `captured`, `absent`, `unavailable`, `redacted` 중 하나다. `redacted`에는 개인정보 정책상의 사유가 필요하다. |

URL은 브라우저가 관찰한 문자열을 그대로 보존한다. 서버가 정규화하거나 쿼리와
프래그먼트를 제거하지 않으며, 파싱할 수 없다는 이유만으로 관찰을 거절하지 않는다.

#### 불투명 활동

```json
{
  "kind": "opaque"
}
```

불투명 활동은 개인정보 필터가 적용된 정상 관찰이다. 대상 앱, 창 제목, 도메인,
해시, 필터 규칙 이름처럼 식별 가능한 정보를 덧붙이지 않는다. 불투명 활동을
수집 공백으로 취급하지 않는다.

### 2.3 수집 상태 변경

잠금, 절전, 사용자 중지, 접근 권한 상실처럼 관찰할 수 없는 상태는 별도 레코드로
보낸다.

```json
{
  "deviceId": "30000000-0000-0000-0000-000000000000",
  "recordType": "collection_state_changed",
  "eventId": "40000000-0000-0000-0000-000000000001",
  "sequence": 413,
  "observedAt": "2026-09-13T10:20:00Z",
  "timezoneId": "Asia/Seoul",
  "utcOffsetMinutes": 540,
  "state": "suspended",
  "reason": "screen_locked"
}
```

`state`는 `active` 또는 `suspended`다. `reason`은 수집 가능성에 관한 이유이며
사용자의 활동 내용을 담지 않는다.

### 2.4 단건 응답과 재시도

서버는 레코드 하나를 저장한 뒤 해당 이벤트의 결과를 반환한다.

```json
{
  "eventId": "40000000-0000-0000-0000-000000000000",
  "status": "accepted",
  "receivedAt": "2026-09-13T10:15:31Z"
}
```

- 처음 저장한 `eventId`는 `accepted`다.
- 같은 `eventId`와 같은 레코드를 다시 보내면 기존 결과를 반환한다.
- 같은 `eventId`와 다른 레코드를 보내면 `409 Conflict`다.
- 동일성은 서버가 생성한 `receivedAt`을 제외한 저장 컬럼과 JSONB payload를 직접
  비교한다. 별도 `event_hash`를 저장하지 않는다.

## 3. 클라이언트 관찰과 개인정보 경계

클라이언트는 현재 포커싱된 앱과 그 포커스 창만 관찰한다. 화면에 보이는 모든 창이나
백그라운드 앱을 나열하지 않는다. 다음 시점에는 새로운 전체 스냅샷을 만든다.

- 수집 시작 또는 중단 후 재개
- 포커싱된 앱 변경
- 포커스 창, 창 제목, 브라우저 탭 또는 URL 변경
- 수집 가능성 변경 이후 다시 관찰할 수 있게 된 시점
- 변화가 없더라도 활성 수집 중 마지막 관찰 후 30초가 지난 시점

식별 정보는 서버에 보내기 전에 클라이언트에서 필터링한다.

| 상황 | 전송 값 |
| --- | --- |
| 개인정보를 저장하면 안 되는 활동 | `{ "kind": "opaque" }` |
| `data:` 또는 `javascript:` URL | URL `redacted`, 사유 `embedded_content_scheme` |
| 허용 길이를 넘는 URL | URL `redacted`, 사유 `length_exceeded` |
| 창 제목을 읽지 못함 | 창 `unavailable`과 수집 실패 사유 |
| 창 자체가 없음 | 창 `absent` |

창·탭 제목은 길이 제한을 넘으면 앞부분, `truncated: true`, 원래 UTF-8 바이트 길이를
함께 보낸다. URL은 일부를 잘라 보내지 않는다. 초기 버전은 오프라인 대기열을
지원하지 않으므로 네트워크가 없는 동안의 관찰 보존을 보장하지 않는다.

## 4. 저장 구조

서버는 기존 `accounts`와 두 신규 테이블만 사용한다.

```text
accounts 1 --- N devices 1 --- N activity_records
```

`activity_records`는 유일한 활동 source of truth다. 공통 식별자·순서·시각은 일반
컬럼으로, 레코드 종류별 본문은 JSONB `payload`로 저장한다.

| 레코드 종류 | `payload` |
| --- | --- |
| `activity_observation` | `{ "context": ... }` |
| `collection_state_changed` | `{ "state": ..., "reason": ... }` |

`UNIQUE(device_id, sequence)`로 한 Device 안의 순번 충돌을 막는다.
별도 batch, stream watermark, 요청 hash, 레코드 hash, 저장 상태 컬럼은 두지 않는다.
전체 컬럼과 제약은 `docs/ERD.md`에 정의한다.

## 5. Device와 인증 경계

한 계정은 여러 Device를 가질 수 있다. 초기 제품은 한 시점에 하나의 Device만
계정에 접속해 활동을 기록한다. 따라서 계정 전체 타임라인은 모든 Device의 레코드를
시간순으로 조회하되, 각 레코드의 Device 출처를 유지한다.

클라이언트는 인증 후 `POST /api/v1/devices`를 호출한다. 요청 전에 UUID
`Idempotency-Key`를 영구 저장하고 응답을 받기 전까지 같은 값을 재사용한다. 서버는
계정과 멱등 키의 조합마다 하나의 `device_id`를 발급하며, 같은 요청의
재시도에는 최초 식별자를 반환한다.

동시에 하나의 기기만 접속하도록 강제하는 기능은 활동 저장 모델의 책임이 아니다.
현재 JWT는 만료 전 즉시 무효화할 수 없으므로 이 제약은 아직 구현된 보장이 아니다.
새 로그인 시 이전 JWT를 즉시 무효화하고 토큰을 Device에 연결하는 작업은
루트 `TODO.md`에 별도로 기록한다. 이번 활동 저장 설계에서는 `accounts`에 인증
상태 컬럼을 추가하지 않는다.

## 6. 조회 시 타임라인 계산

초기 버전은 `activity_timeline_segments` 같은 projection 테이블을 만들지 않는다.
서버는 조회 기간의 원본을 기기별 `sequence`와 `observed_at`에 따라 읽고 다음 결과를
계산한다.

- 같은 활동 문맥이 연속되면 하나의 활동 구간으로 묶는다.
- 문맥이 달라지면 이전 구간을 끝내고 새 구간을 시작한다.
- 연속된 불투명 활동은 하나의 불투명 활동 구간으로 묶을 수 있다.
- 불투명 활동과 상세 활동은 서로 합치지 않는다.
- `suspended`는 진행 중인 활동을 끝내고 수집 공백을 시작한다.
- 재개 뒤 첫 관찰은 새 활동 구간을 시작한다.
- 순번 누락이나 해석할 수 없는 레코드 앞뒤는 이어 붙이지 않는다.

조회 결과는 다음 표시 규칙을 따른다.

| 계산된 문맥 | 표시 |
| --- | --- |
| 앱 정보가 있고 창 정보가 없거나 수집되지 않음 | 앱 활동 |
| URL만 정상인 브라우저 문맥 | 웹 활동 |
| 불투명 문맥 | 알 수 없는 활동 |
| 표시할 앱·웹 식별 정보가 전혀 없음 | 카드 미표시 |
| 수집 공백 | 공백 |

활동 구간과 `CaptureGap`은 조회 응답이며 영구 저장된 원본이 아니다. 조회 비용이나
페이지 경계 계산이 실제 병목으로 확인되면 원본에서 재생성할 수 있는 projection을
후속 모델로 추가한다.

## 7. 보존과 비범위

- 서버는 수락한 개인정보 필터 후 활동 레코드를 원본으로 저장한다.
- 저장하면 안 되는 식별 정보는 전송 전에 클라이언트에서 제거한다.
- 원본 활동, AI 해석, 사용자가 확인한 의미를 같은 데이터에 섞지 않는다.
- 보존 기간, 삭제, 내보내기 정책의 구체 값은 이번 설계에서 정하지 않는다.
- batch 전송과 오프라인 대기열은 초기 범위에서 제외한다.
- AI 해석, 사용자 확정 의미, 통계, 집중도는 별도 후속 모델로 다룬다.
