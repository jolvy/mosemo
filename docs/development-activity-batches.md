# 개발용 활동 배치 전달

`POST /api/v1/activities/batches`는 Bearer 계정과 Device 귀속을 확인하고 다음 UTF-8 JSON을 메모리에 수락한다. 기존 단건 POST는 제거했다. 타임라인 GET, 라벨, 집중 세션, 계정 API 계약은 유지한다.

```json
{
  "batchId": "11111111-1111-1111-1111-111111111111",
  "deviceId": "22222222-2222-2222-2222-222222222222",
  "records": [{
    "eventId": "33333333-3333-3333-3333-333333333333",
    "sequence": 10,
    "recordType": "activity_observation",
    "observedAt": "2026-09-14T00:00:00.123456Z",
    "timezoneId": "Asia/Seoul",
    "utcOffsetMinutes": 540,
    "context": {"kind": "opaque"}
  }]
}
```

개별 원본에는 deviceId/accountId를 넣지 않는다. records는 1~200건, 실제 HTTP 본문 전체(공백·envelope 포함)는 512 KiB 이하다. 본문 수신 중 제한하며 Content-Length를 신뢰하지 않는다. 개인정보·UTC·문맥 discriminator·suspended focus 검증은 요청 경계에서 적용한다. 스키마 422, 인증 401, Device 미등록/다른 계정 404, 크기 초과 413, 전달 불가 503이다. 503은 공통 오류와 `Retry-After: 1`을 반환한다.

202 응답은 `{"batchId":"...","status":"accepted"}`뿐이다. DB 저장 완료, 내구성 있는 Kafka ACK, Kafka offset commit을 뜻하지 않는다. 클라이언트는 같은 batchId/내용으로 미확인 응답을 재전송하고, 수락 뒤 DB 타임라인을 polling한다. 서버 consumer 거절은 즉시 HTTP 409로 반환하지 않는다. 클라이언트 변경은 별도 작업이다.

## 실행과 종료

공개 `TestKafkaBroker(connect_only=True)`와 faststream_fastapi lifespan을 사용한다. bounded queue → 고정 Worker 1개 → 실제 broker.publish → 등록 subscriber → 메시지별 SessionFactory/transaction 순서다. HTTP Request/session/model은 전달 메시지에 넣지 않는다. 원본 byte snapshot을 내부 JSON 문자열로 손실 없이 운반하고 인증 계정은 서버가 만든 header로 전달한다. HTTP app DI, 오류 middleware, HTTP client lifespan, 실제 route OpenAPI는 내부 FastAPI에 유지한다. 외부 Kafka 연결은 없다. 시작 후 공개 handler `reset_test()`로 TestBroker 호출 기록을 비활성화해 성공 원본이 Mock/call recorder에 무제한 누적되지 않게 한다. subscriber와 DI 처리는 유지한다.

`BATCH_ENABLED` 기본 true, `BATCH_CAPACITY` 기본 64이며 `MOSEMO_ENV=prod/production`에서는 메모리 전달을 끈다. 이 숫자는 측정한 최적값이 아니다. 대기·active·failed·blocked를 합쳐 64개까지 보관하므로 원본 최대 32 MiB이며 객체/내부 publish JSON/요청 처리 중 버퍼는 별도다. 동시에 들어오는 HTTP 요청 전체에 대한 process-wide memory cap은 아니다. 실패 원본은 자동 eviction하지 않는다. 용량 한도, 미시작/중단/fatal Worker에서는 새 수락을 503으로 거절한다.

종료는 수락 중지 → Worker cancel/await → 메시지 DI finally/rollback → broker close → HTTP 자원 종료/engine dispose다. 개발 중 프로세스 종료로 미처리 원본이 유실될 수 있다. Kafka parking topic·partition/group 직렬화·offset·운영 내구성은 구현하지 않았다.

일시적인 연결/DB serialization/deadlock/lock timeout은 최초 포함 5회, full jitter 상한 1/2/4/8초로 재시도한다. 중복/sequence 거절은 정상 처리 결과다. focus 소유권/시간 오류와 관찰 시각 역행은 배치 전체 rollback 및 terminal failure로 보관한다. 원문/URL/title/token/traceback은 실패 로그에 남기지 않는다. 같은 계정/Device의 후속 수락 원본은 admission 순서로 blocked 보관하며 다른 Device/계정은 계속 처리한다.

개발 중 Python에서 `app.state.batch_runtime.delivery.inspect()`로 복사된 보관 상태를 보고 `reprocess(retained_id)`로 실패한 선두를 다시 처리할 수 있다. payload가 있는 개발자 전용 함수로, 공개 관리 API가 아니다. 원본 수정/삭제 기능은 없고 정책 오류가 해소되지 않으면 재처리는 다시 실패한다. 재처리 성공 후 blocked 후속 원본을 원래 순서로 해제한다.

## Consumer 트랜잭션과 비용

새 migration `c9d0e1f2a3b4`는 account/device/batchId 복합 PK의 receipt와 replay ordering index를 추가한다. HTTP admission에서 receipt를 읽거나 쓰지 않는다. consumer가 계정 advisory lock을 유지한 채 receipt claim, 원본 채택, 구간 변경, 결과를 같은 transaction으로 commit한다. `ON CONFLICT DO NOTHING` 뒤 DB winner를 다시 읽는다.

동일 scope/ID의 같은 정규화 내용은 기존 결과로 안전 재처리한다. 내용 fingerprint는 검증된 JSON(기본값 포함)을 key 정렬하여 SHA-256으로 계산한다. 다른 내용은 기존 receipt/원본을 덮어쓰지 않고 `batch_content_conflict`로 메모리에 보관한다. processed eventId는 같은 Device에서 무시하며 다른 Device의 eventId를 재사용하거나 다른 계정 결과를 노출하지 않는다. Device sequence는 first incoming winner를 유지하고 충돌 원본만 `sequence_rejected`, 나머지는 commit한다. 배치 내부 승자는 입력 배열에서 먼저 온 원본이다. 결과 배열은 입력 순서다.

receipt는 64 hex 문자 fingerprint, 원본 bytes 최대 512 KiB, 원본별 ID/결과 JSON 최대 200건, scope UUID 3개와 처리시각을 추가 저장한다. index·row/TOAST/WAL 비용도 있다. 별도 HTTP receipt DB round trip은 없지만 consumer receipt INSERT/UPDATE가 추가되며 각 원본의 존재/sequence/clock neighbor 확인은 최대 건수에 비례한다. 계정의 최초 consumer receipt 전에는 기존 원본의 Device별 시간 역행을 DB window query로 한 번 검사한다. 이 초기 검사는 기존 계정 원본 수에 비례하며, 이후 입력은 neighbor 검사로 같은 불변식을 유지한다. 신규 원본 한 건은 기본적으로 event/sequence pre-read 2회, INSERT 1회, clock neighbor SELECT 최대 2회를 쓰며 focus가 있으면 SELECT 1회가 추가된다. 따라서 정상 신규 200건의 원본 처리만 최대 약 1,000회 SQL(집중 연관이 모두 있으면 약 1,200회)이 되고, receipt/lock/ownership/projection SQL은 별도다. bulk SQL 최적화는 아직 하지 않았다. 원본 rejected 내용도 receipt source에 유지하려고 저장 비용을 선택했다. receipt/source의 운영 retention은 이번 범위에서 정하지 않았다. 기존 원본 UNIQUE만으로는 같은 batchId의 내용 변경을 재시작/다중 HTTP process에서 감지할 수 없으므로 DB ledger를 선택했다.

## 순서와 재구성

Device 내 순서는 sequence이고 contiguous 조건은 없다. 미사용 낮은 sequence도 저장한다. Device별 nondecreasing observedAt chain을 `(observedAt, deviceId, sequence, eventId)`로 merge한다. 다른 Device의 sequence를 서로 비교하지 않는다. Device tie에 UUID를 사용해 수신 순서 변화에 영향을 받지 않는 결정성을 택했다. 구간은 기존 계정 전체 문맥으로 계산하고 응답에서 출처 Device를 추가하지 않는다.

정상 append는 마지막 구간 상태 + 신규 채택 원본만 계산한다. 과거 변경은 배치 전체를 묶어 predecessor 시작 경계에서 128행 keyset page로 읽고, 모든 신규 원본을 지난 뒤 기존 closed segment 전체 상태와 일치하는 안전한 convergence에서 멈춘다. 이후 suffix 원본을 미리 읽지 않는다. 첫/병합된 구간의 기존 ID anchoring과 content-version 계산은 유지한다. 변경 prefix 구간/계산 상태 메모리는 실제 영향범위에 비례한다. 성능은 측정하지 않았다.

같은 Device에서 `seq10 at 10:00:10`, `seq11 at 10:00:05`처럼 시간이 역행하면 순서를 지키면서 비음수 구간을 만들 수 없다. 이 구현은 시간을 clamp하거나 가짜 gap을 만들지 않는다. `projection_time_regression`으로 rollback하고 원본을 보관한다. 단조 시계 및 실제 제품 복구 정책은 PO 결정이 필요하다. 잘못된 focus 연관도 silent discard하지 않고 전체 배치 failure로 보관한다.

라벨 회귀 fixture는 과거 sequence를 단순 고유 token으로 사용했으므로, 관찰 시각의 실제 순서에 맞는 고유 sequence로 전환했다. 관찰 시각/원본 ID/문맥/라벨 기대값은 유지했다. offline replay 테스트는 명시적인 sequence, gaps, 낮은 sequence, 다른 Device tie, 같은 시각 sequence 우선순위를 별도로 검사한다.

## 검증

unit/component의 gate 테스트는 실제 FastStream subscriber/DI를 통과하지만 DB 동작을 증명하지 않는다. `tests/integration/test_activity_batches.py`는 기본 main 구성과 실제 PostgreSQL에서 HTTP 선반환, 독립 세션, commit/atomic rollback, receipt, replay/reference 및 page convergence를 검사한다. Docker 소켓이 차단된 sandbox에서는 이 통합 검증을 완료할 수 없으며 PO host에서 전체 suite를 실행해야 한다.
