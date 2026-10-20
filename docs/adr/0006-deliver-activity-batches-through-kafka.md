---
status: accepted
date: 2026-10-17
---
# 활동 배치는 독립된 Kafka 브로커로 전달하고 발행 ACK 후 수락 응답한다

## 맥락

활동 배치 HTTP 요청은 원본 저장과 타임라인 계산이 끝나기 전에 수락 여부를
반환한다. 전환 전 구현은 제한된 메모리 큐와 Worker, TestKafkaBroker를 통해
FastStream Handler를 실행한다. 따라서 202 이후에도 API 프로세스가 종료되면
처리되지 않은 원본이 사라질 수 있고 실제 Kafka의 발행 실패·재전달·offset을
검증할 수 없다.

활동 배치는 애플리케이션과 독립된 Kafka 브로커를 통해 전달한다. HTTP 수락의 의미, 원본과
타임라인의 DB 원자성, 중복 안전성을 구분해야 한다. 특정 Device의 실패 격리 요구는 후속 범위로 유지하며 이번 정상 전달 경로와 구분한다.

선택 기준은 실제 브로커 전달의 검증 가능성, 수락 후 API 종료에 대한 원본 보존,
재전달의 중복 안전성, 환경별 연결 구성과 실패 복구 복잡성이다.

## 결정

### 확정한 방향

2026-10-18 설계 대화에서 아래 메시지·실행 계약을 승인했다. 계획은
[Plan Issue #61](https://github.com/jolvy/mosemo/issues/61)에서 관리하며 구현 완료를 뜻하지 않는다.

- 로컬은 Docker Kafka와 영속 volume을 사용하고, 환경별 연결 설정을 주입한다.
  실제 Kafka를 사용하며 TestBroker는 테스트 대역으로만 둔다. HTTP 수락용 메모리
  큐·발행 Worker를 제거한다. 운영 broker 배포·복제 정책은 별도 범위다.
- 인증·입력·Device 귀속 확인 후 배치 전체를 발행한다. `acks=all`과 producer
  idempotence를 사용하고 ACK 확인 후 `202 {"batchId": "...", "status": "accepted"}`를
  반환한다. DB 처리 완료·원본별 성공·consumer offset commit을 기다리는 응답이 아니다.
- 발행 불가·ACK 제한시간 초과는 기존 503·Retry-After 계약을 따른다. 미확인이
  미발행을 뜻하지 않으므로 같은 batchId·내용 재시도와 DB 중복 방어를 유지한다.
- 별도 Pydantic 메시지 모델을 사용하고 HTTP 입력 검증 규칙을 재사용한다.
  value는 `accountId`, `batchId`, `deviceId`, `records`를 담은 JSON이다.
  Publisher가 인증 결과로 value의 accountId를 채우고 그 값의 UTF-8 UUID 문자열을
  key로 생성한다. accountId header는 사용하지 않으며 Consumer가 key와 value의
  계정 일치를 검증한다. HTTP 요청 모델은 유지하고 원본 bytes 보존·이중 JSON 포장은 요구하지 않는다.
- 토픽은 초기 partition 2개다. 개수는 토픽 초기화 구성에서 주입하고 Publisher는
  번호·개수를 직접 지정하지 않고 기본 key partitioner를 사용한다.
  같은 계정은 같은 partition에 모이며 여러 계정이 partition을 공유할 수 있다.
- partition 내부에서는 배치의 DB commit과 해당 처리 위치의 offset commit을
  마친 뒤 다음 배치를 처리한다. partition 간은 병렬로 최대 두 배치를 처리한다.
  다른 partition의 미처리 위치를 넘어 commit하지 않는다. 보장하는 순서는 Kafka
  기록 순서이며 동시 HTTP 요청 시작 순서가 아니다. Kafka는 계정 advisory lock을
  사용하지 않는다. 같은 Topic·Consumer Group·계정 Key 매핑을 유지하고 같은 계정의
  legacy 동시 사용을 금지한다. 경로 전환 전 기존 처리를 종료한다. 트랜잭션과
  legacy 전용 Lock의 책임은 ADR 0007과 Plan #74의 승인된 변경을 따른다.
- API와 Consumer는 같은 저장소·업무 코드를 공유하되 별도 진입점·프로세스로 실행한다.
- 유효한 committed offset이 있으면 이어서 처리하고, 없는 경우에만
  `auto_offset_reset=earliest`로 현재 보관된 가장 오래된 위치부터 처리한다.
- 채택 원본·타임라인·receipt를 같은 DB 트랜잭션에서 commit한 뒤 offset을 확정한다.
  두 commit은 원자적이지 않으므로 그 사이 종료·재전달은 receipt로 안전하게 처리한다.
  producer idempotence가 HTTP 재요청·DB 중복까지 제거한다고 간주하지 않는다.
- Consumer 정상 종료는 새 배치 실행을 멈추고 진행 중인 최대 두 배치의 DB·offset commit을
  최대 30초 기다린 뒤 자원을 정리한다. 전체 backlog를 비우는 종료가 아니며
  FastAPI/Uvicorn의 종료 대기 설정과 구분한다.
- Kafka 발행 확인은 최대 5초 기다리며 기존 HTTP 전체 수락 제한도 유지한다.
  제한시간 내 ACK를 확인하지 못하면 기존 503 계약을 적용한다.
- Publisher에 별도 동시 발행 수 제한이나 내부 Kafka 메시지 크기 추가 제한은 두지 않는다.
  기존 HTTP 본문 512 KiB와 Kafka·클라이언트 자체 한도는 유지한다. 최대 유효 HTTP
  입력이 내부 메시지로 직렬화된 뒤에도 전달 가능한지 검증한다.

### 후속 범위와 미결정

- 실패·복구의 논의·설계·구현 자체를 후속으로 넘긴다. 실패 Device 보류, 다른 Device
  처리 지속, 실패 보관·재처리는 기존 요구로 남기되 이번 완료 조건으로 두지 않는다.
- 객체지도 v1의 `BatchFailureHandler`와 `BatchRecoveryService`는 경계만 마련하고
  로직은 채우지 않는다. Consumer 중단·partition 차단·자동 재시도·parking topic 중
  어느 정책도 이 결정으로 채택하지 않는다. 빈 객체를 처리 성공·offset 확정 근거로 쓰지 않는다.
- group/topic 이름, broker 이미지 버전과 환경별 retention·디스크 상한은 실행 구성에서
  구체화한다. 발행 확인 5초·Consumer 종료 30초와 추가 제한을 두지 않는 결정은 확정됐다.
- 재할당 중 진행 중 처리와 종료·offset 안전성은 구현 검증 대상이다. 실패·복구 정책
  미구현 상태를 운영 전환 완료로 표현하지 않는다.

이 ADR의 결정과 초기 실행 수치는 승인됐다. 실패·복구는 후속 범위로 남긴다.

## 고려한 대안

| 대안 | 장점 | 비용·제약 | 선택 |
| --- | --- | --- | --- |
| 메모리 큐와 TestBroker 유지 | 외부 Kafka 없이 실행 가능하고 현재 동작을 유지한다. | API 종료 시 미처리 원본 유실, 실제 브로커·offset·재할당 검증 불가. | 전환 대상 |
| 독립된 Kafka 브로커에 발행 후 ACK를 기다림 | 수락 원본을 API 메모리와 분리하고 환경별 브로커에 같은 전달 계약을 적용한다. | 브로커 왕복 지연, Kafka 인프라·저장 공간, consumer 복구 설계가 필요하다. | 채택 방향 |
| HTTP 요청에서 DB 처리 완료 후 응답 | 응답과 DB 결과의 관계가 단순하고 별도 전달 인프라가 없다. | 응답이 전체 계산·DB 처리 시간에 종속되며 수락과 비동기 처리를 분리하는 목표에 맞지 않는다. | 미채택 |

## 결과와 트레이드오프

- 별도 동시 발행 수 제한을 두지 않아 구현은 단순하지만 broker 지연 시 API 대기
  요청이 증가할 수 있다. 발행 5초 제한은 API 전체 메모리 상한을 보장하지 않는다.
- 내부 메시지 크기 추가 한도를 두지 않으므로 별도의 애플리케이션 거절 기준은
  만들지 않는다. Kafka·클라이언트 자체 제약과 직렬화 크기의 호환성은 검증한다.
- Consumer 종료 30초는 초기 실행값이며 처리량·응답 지연의 성능 보장은 아니다.

- 정상 HTTP 수락 경로가 Kafka 가용성과 발행 ACK 지연에 의존한다. 메모리 수락
  응답과 같은 지연이라고 가정하지 않는다.
- 수락한 원본은 API 프로세스 메모리에만 존재하지 않는다. 보존·장애 내성의 범위는
  연결한 Kafka의 복제·저장·retention 설정에 달려 있다. 로컬 Docker 개발 구성의
  단일 브로커·volume 특성을 운영 환경의 보장으로 일반화하지 않는다.
- 현재 메모리 경로의 Worker 재시도·실패 보관·Device별 보류는 그대로 사용할 수
  있는 Kafka offset 정책이 아니다. 실패·복구 정책은 후속에서 결정하고 정상 전달 경로는 실제 브로커로 검증한다.
- 검증에는 발행 ACK 전후 응답, ACK 미확인 후 동일 배치 재시도, API 재시작,
  DB commit 직후 consumer 종료와 재전달, 두 partition의 순차·병렬 처리와
  offset 재개·정상 종료를 포함한다. 실패 Device 보류·다른 Device 처리 지속 검증은 후속이다.
- 최초 이력 정렬 전환의 부분 재구성 최적화는 별도 과제이며 이번 전달 경로
  변경과 결합하지 않는다.

## 대체한 ADR

없음. 메모리 전달만을 규정한 기존 ADR은 없다. ADR 0004의 쓰기 경계와 원본
정렬·이력 전환 변경은 ADR 0007·0008에서 다룬다.

## 연관 PRD·ADR

- [배치 receipt·트랜잭션 ADR](0007-store-batch-receipts-with-activity-transactions.md)
- [관찰 순서·이력 전환 ADR](0008-order-observations-and-transition-existing-timelines.md)
- [활동 배치 PRD](../prd/activity-batches.md)
- [활동 배치 요구사항](../requirements/activity-batches.md)
- [쓰기 시 관찰 projection ADR](0004-project-observation-timeline-on-write.md)
- [Apache Kafka Docker 이미지](https://kafka.apache.org/41/getting-started/docker/)
- [Apache Kafka producer ACK 설정](https://kafka.apache.org/40/configuration/producer-configs/)

2026-10-18 승인된 계약을 반영했다. 실패·복구는 후속이며 구현 검증 전이다.

2026-10-18 사용자가 ADR 0006~0009 모두를 명시적으로 승인하여 accepted로 전환했다. 구현·검증 완료를 뜻하지 않는다.

2026-10-18 사용자의 “0006에 반영해” 지시에 따라 본 ADR을 직접 갱신했다. 발행 확인
5초·Consumer 종료 대기 30초·Publisher 동시 발행 및 내부 메시지 크기 추가 제한 없음의
승인을 반영하며, 새 ADR을 생성하지 않는다. 구현·검증 완료를 뜻하지 않는다.
