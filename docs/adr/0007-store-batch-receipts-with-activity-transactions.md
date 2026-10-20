---
status: accepted
date: 2026-10-17
---
# 배치 처리 receipt를 원본·타임라인과 같은 DB 트랜잭션에 저장한다

## 맥락

활동 배치의 전달 수락과 DB 반영은 별도 단계다. 클라이언트의 응답 미확인
재시도와 consumer 재전달로 같은 배치가 여러 번 처리될 수 있다. 이미 반영한
배치를 다시 반영하지 않고, 같은 batchId를 다른 내용으로 사용한 경우도 구분해야 한다.

원본의 eventId와 Device별 sequence UNIQUE 제약은 원본 중복·충돌을 보호하지만,
어떤 배치가 어떤 내용으로 처리됐는지 또는 거절 원본을 포함한 처리 결과까지
표현하지 않는다. API 프로세스의 메모리 기록만으로는 재시작·다중 프로세스에서
같은 판정을 유지할 수 없다.

선택 기준은 재시도 안전성, 계정·Device 격리, 채택 원본과 타임라인의 원자성,
배치 내용 변경 감지와 추가 저장·조회 비용이다.

## 결정

- DB에 배치 처리 receipt를 저장한다. 식별 범위는
  `(accountId, deviceId, batchId)`이며 이 조합을 기본 키로 사용한다.
- receipt에는 검증된 배치 내용의 fingerprint, 입력 순서에 따른 원본별 처리 결과와
  처리 시각을 저장한다. 원본 요청 bytes인 source는 저장하지 않는다. 이는 HTTP 전달 수락 증명이나
  Kafka offset 상태가 아니라 DB 배치 처리 결과다.
- fingerprint는 검증된 요청 모델의 JSON 표현을 사용한다. 기본값을 포함하고
  object key를 정렬한 뒤 SHA-256으로 계산한다. 배열 순서는 보존한다.
  JSON 공백·object key 순서 차이만 있는 재전송과 검증된 내용 변경을 구분한다.
  대상은 batchId·deviceId·records이며 accountId는 식별 범위다. Kafka key·header는
  해시 대상이 아니다. 해시는 64자리 16진수 문자열이다.
- HTTP 수락 경로에서 receipt를 조회·저장하지 않는다. Kafka의
  `BatchProcessingExecutor`는 시도별 독립 Session과 트랜잭션을 소유한다.
  legacy 직접 처리의 `LegacyBatchExecutor`는 요청 Session에서 트랜잭션을 열고
  계정 advisory lock을 획득한다. 공통 `ActivityBatchProcessor`는 열린 트랜잭션에서
  Device 귀속 확인과 원본·타임라인·receipt 처리를 수행하며 Lock·commit·rollback·ACK를 소유하지 않는다.
- Kafka는 DB 계정 advisory lock 없이 같은 Topic·Consumer Group·계정 Key 매핑과
  Partition별 순차 실행으로 직렬화한다. 같은 계정의 Kafka·legacy 동시 사용은 금지하며
  경로 전환 전에 기존 경로의 해당 계정 처리를 종료한다. DB 커밋 이후에만
  Kafka ACK 또는 legacy 성공 응답을 반환한다.
- receipt claim, 원본별 채택·거절, 채택 원본 저장, 타임라인 갱신과 처리 결과
  저장을 하나의 DB 트랜잭션에서 수행한다. DB 장애·집중 세션 연결 오류·관찰
  시각 역행으로 배치가 실패하면 receipt를 포함한 변경 전체를 롤백한다.
- receipt claim은 DB 기본 키와 `ON CONFLICT DO NOTHING`으로 보호한다.
  기존 receipt가 있으면 다시 읽어 fingerprint를 비교한다. 같은 내용이면 기존
  결과를 반환하고 원본·타임라인을 중복 반영하지 않는다. 다른 내용이면
  `batch_content_conflict`로 거절하고 기존 결과를 보존한다.
- eventId와 `(deviceId, sequence)`의 기존 UNIQUE 제약을 유지한다. 원본 INSERT의
  충돌은 DB에서 결정된 기존 원본을 다시 읽어 판정한다. receipt는 원본별
  중복·충돌 검사와 계정 격리를 대신하지 않는다.
- legacy의 3초 잠금 경합은 해당 트랜잭션을 롤백하고 기존 HTTP 503 계약을 유지한다. 재시도·실패·복구 정책의
  논의는 후속으로 넘기며 이미 완료된 HTTP 수락 응답을 DB 처리 오류로 바꾸지 않는다.
- 2026-10-18 사용자 승인에 따라 source 모델·저장·Processor bytes 인자를 제거한다.
  운영·보존 데이터가 없다는 전제로 기존 receipt 생성 migration에서 컬럼을 직접
  제거하며 새 migration·데이터 이관은 만들지 않는다. 기존 로컬 DB reset은 별도 실행 요청 없이 수행하지 않는다.

Kafka 발행 ACK와 consumer offset commit의 경계는 ADR 0006에서 다룬다.
DB commit 후 재전달에도 receipt로 중복 반영을 막지만, DB와 Kafka를 하나의
원자적 트랜잭션으로 처리한다고 주장하지 않는다.

정상 경로와 commit 사이 종료·재전달의 재현 테스트는
[README](../../README.md#real-broker-tests)에 연결하며, 실제 실행 증거는
[검증 Issue #66](https://github.com/jolvy/mosemo/issues/66)에서 관리한다.
실패·복구 경로는 미구현이며 이 receipt 결정의 검증을 운영 준비 완료로 해석하지 않는다.

## 고려한 대안

| 대안 | 장점 | 비용·제약 | 선택 |
| --- | --- | --- | --- |
| 원본 UNIQUE 제약만 사용 | 추가 receipt 저장과 조회가 없다. | 같은 batchId의 내용 변경과 배치별 처리 결과를 원본 제약만으로 알 수 없다. 거절된 원본은 저장 원본에 남지 않는다. | 미채택 |
| 메모리에서 batchId와 결과 관리 | 구현과 로컬 조회가 단순하다. | 재시작·다중 프로세스에서 같은 배치 판정을 유지할 수 없다. 원본·타임라인과 원자적 저장도 어렵다. | 미채택 |
| DB receipt를 원본·타임라인과 함께 저장 | 배치 단위 재시도·내용 변경·처리 결과를 DB 반영과 함께 보호한다. | receipt 쓰기와 조회, fingerprint·결과·index의 저장 비용이 추가된다. | 채택안 |

## 결과와 트레이드오프

- HTTP 수락 경로에는 receipt DB 왕복을 추가하지 않지만 Handler에는 receipt
  claim·조회·결과 저장 비용이 생긴다. 계정별 잠금 대기 비용은 legacy에만 남는다.
- 요청 본문 전체를 보존하지 않으므로 receipt에서 거절 원본의 전체 내용을 복원할 수
  없다. 대신 fingerprint로 내용 변경을 감지하고 원본별 stored·duplicate·거절 결과를
  입력 순서로 보존한다. 실패 보관소를 대신하는 용도로 receipt를 확장하지 않는다.
- receipt는 처리 완료 시에만 commit된다. 실패한 배치의 보관·보류·재처리는
  별도 전달·복구 경로의 책임이며 receipt를 실패 큐로 사용하지 않는다.
- 원본별 조회·INSERT·시각 검사의 비용은 원본 수에 따라 증가한다. bulk 처리
  최적화는 중복·충돌 판정과 원자성을 유지하는 별도 작업으로 다룬다.
- 운영 retention과 fingerprint·결과의 보존 기간은 미결정이다. receipt를 삭제하면
  해당 batchId의 과거 내용과 결과를 재사용하는 보장이 달라지므로 정책 없이 삭제하지 않는다.
- 현재 구현은 계정 receipt의 존재를 최초 정렬 전환 완료의 근거로도 사용한다.
  retention 도입 시 마지막 receipt 삭제가 전환을 다시 유발하는 결합을 검토해야 한다.
- 검증 대상은 동일 배치 재시도, 내용 변경, 배치 내부 중복, 원본별 충돌,
  동시 처리의 기존 원본 재조회, 계정 격리, commit·전체 rollback과 재시작 후 재전달이다.

## 대체한 ADR

[ADR 0004](0004-project-observation-timeline-on-write.md)의 쓰기 경계를 일부
대체한다(supersedes in part). 원본·projection 원자성을 유지하며 Kafka DB 쓰기는
Handler에서 receipt와 함께 수행한다. 2026-10-20 승인된 Executor 분리에 따라
계정 advisory lock은 legacy에만 적용한다. Kafka의 처리 오류는 이미 완료된
수락 응답을 변경하지 않는다.

원본 정렬·기존 이력 전환의 변경 범위는 ADR 0008에서 다룬다. 읽기 모델 구조,
날짜 경계와 Device 출처 비노출 등 ADR 0004의 나머지 결정은 유지한다.

## 연관 PRD·ADR

- [활동 배치 요구사항](../requirements/activity-batches.md)
- [활동 배치 PRD](../prd/activity-batches.md)
- [관찰 타임라인 PRD](../prd/observation-timeline.md)
- [Kafka 전달 ADR](0006-deliver-activity-batches-through-kafka.md)
- [관찰 순서·이력 전환 ADR](0008-order-observations-and-transition-existing-timelines.md)
- [쓰기 시 관찰 projection ADR](0004-project-observation-timeline-on-write.md)

2026-10-18 fingerprint 유지·source 제거·기존 생성 migration 수정 승인을 반영했다.
실패·복구 정책은 후속이며 본 ADR의 receipt 결정은 승인됐다.

2026-10-18 사용자가 ADR 0006~0009 모두를 명시적으로 승인하여 accepted로 전환했다. 구현·검증 완료를 뜻하지 않는다.

2026-10-20 [Plan #74](https://github.com/jolvy/mosemo/issues/74)의 사용자 승인에 따라
Executor의 트랜잭션 소유와 legacy 전용 Lock, 동일 계정 경로 혼용 금지를 반영했다.
