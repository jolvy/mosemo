---
status: proposed
date: 2026-10-17
---
# 활동 배치는 독립된 Kafka 브로커로 전달하고 발행 ACK 후 수락 응답한다

## 맥락

활동 배치 HTTP 요청은 원본 저장과 타임라인 계산이 끝나기 전에 수락 여부를
반환한다. 현재 구현은 제한된 메모리 큐와 Worker, TestKafkaBroker를 통해
FastStream Handler를 실행한다. 따라서 202 이후에도 API 프로세스가 종료되면
처리되지 않은 원본이 사라질 수 있고 실제 Kafka의 발행 실패·재전달·offset을
검증할 수 없다.

활동 배치는 애플리케이션과 독립된 Kafka 브로커를 통해 전달한다. HTTP 수락의 의미, 원본과
타임라인의 DB 원자성, 중복 안전성을 구분해야 한다. 특정 Device의 처리 실패가
다른 Device의 처리를 막지 않는 기존 요구도 유지해야 한다.

선택 기준은 실제 브로커 전달의 검증 가능성, 수락 후 API 종료에 대한 원본 보존,
재전달의 중복 안전성, 환경별 연결 구성과 실패 복구 복잡성이다.

## 결정

### 확정한 방향

- 활동 배치의 전달 경로를 애플리케이션과 독립된 Kafka 브로커로 전환한다.
  메모리 큐에 넣었다는 사실만으로 HTTP 수락을 완료하지 않는다.
- Kafka 연결 정보는 환경별 실행 설정으로 주입한다. 개발 환경에서는 로컬 Docker로
  Kafka를 실행하지만, 운영 환경의 Kafka 실행 위치·배포 방식은 이 결정으로 제한하지 않는다.
- 인증·입력·Device 귀속 확인 후 배치 전체를 발행하고, 브로커의 발행 ACK를
  확인한 뒤 `202 {"batchId": "...", "status": "accepted"}`를 반환한다.
- 202는 Kafka 발행 수락을 뜻하며 Handler 실행, DB commit, 원본별 성공이나
  consumer offset commit을 뜻하지 않는다.
- 발행 실패 또는 제한 시간 내 ACK 미확인에는 성공을 반환하지 않는다. ACK를
  확인하지 못해도 브로커에는 기록됐을 수 있으므로 클라이언트는 동일 batchId와
  내용으로 재시도하고 DB의 중복 처리 방어를 유지한다.
- 집중 세션 연결 오류의 배치 전체 롤백, 실패 Device의 후속 배치 보류·재처리,
  다른 Device 처리 지속이라는 제품 동작은 유지한다.

### 구현 전 검토할 추천 설계

- 로컬 개발은 단일 Kafka 브로커와 영속 Docker volume으로 시작한다. 브로커
  버전과 실행 설정은 재현 가능하게 고정한다. 이는 개발 환경의 구성안이며 운영
  브로커 수·배포 방식·복제 정책은 별도로 정한다.
- 발행 ACK는 `acks=all`을 추천한다. 단일 브로커·복제 계수 1에서는 복제본 장애
  내성을 제공하지 않으며, ACK를 전원 장애·디스크 손실까지의 절대 보존으로 설명하지 않는다.
- FastStream을 유지하고 실제 Kafka 연결을 사용한다. TestBroker는 테스트 대역으로만
  사용한다. HTTP 수락용 메모리 큐와 발행 Worker를 별도 수락 경계로 유지하지 않는다.
- 채택 원본·타임라인·배치 receipt는 기존 DB 트랜잭션으로 함께 반영한다.
  DB commit 후 Kafka offset을 commit하는 방향을 추천한다. 두 commit은 원자적이지
  않으므로 DB commit 직후 종료에 따른 재전달을 receipt로 안전하게 처리해야 한다.
- producer의 중복 억제만으로 HTTP 재시도·consumer 재전달의 DB 중복을 해결했다고
  간주하지 않는다. batchId·eventId·Device sequence의 기존 보호를 유지한다.
- Kafka key는 계정 단위를 우선 검토한다. 계정 타임라인을 여러 Device가 공유하므로
  같은 계정 입력을 모을 수 있지만, 실제 동일 계정 처리 직렬화는 DB 잠금도 유지한다.
  실패 Device만 보류하는 요구를 Kafka key만으로 충족한다고 가정하지 않는다.

### 미결정

- 실패 배치와 같은 Device의 후속 배치를 어디에 보관하고, 재시작·재할당 후
  보류 상태와 원래 처리 순서를 어떻게 복구할지 결정해야 한다.
- partition 전체를 중단하면 함께 배치된 다른 Device도 막힐 수 있다. 반대로
  실패 원본을 메모리에만 두고 그 offset을 넘겨 commit하면 종료 시 복구 경로를
  잃을 수 있다. 별도 영속 실패·보류 저장소, Kafka 실패 경로, offset을 유지한
  제한적 병렬 처리의 대안을 비교한 뒤 수동 commit과 재처리 경계를 확정한다.
- consumer group, partition 수, key의 최종 선택, consumer 실행 프로세스,
  재시도 중 poll·재할당 처리와 종료 시 commit 경계를 확정해야 한다.
- 브로커 ACK 대기 시간, producer 전송 용량, 실제 메시지 envelope를 포함한
  broker·consumer 크기 제한, 환경별 retention과 디스크 상한을 정해야 한다.

이 ADR은 전달 경로와 HTTP 수락 시점을 기록한다. 추천 구성과 미결정 항목은
추가 검토가 필요하므로 ADR 전체 상태는 proposed로 둔다.

## 고려한 대안

| 대안 | 장점 | 비용·제약 | 선택 |
| --- | --- | --- | --- |
| 메모리 큐와 TestBroker 유지 | 외부 Kafka 없이 실행 가능하고 현재 동작을 유지한다. | API 종료 시 미처리 원본 유실, 실제 브로커·offset·재할당 검증 불가. | 전환 대상 |
| 독립된 Kafka 브로커에 발행 후 ACK를 기다림 | 수락 원본을 API 메모리와 분리하고 환경별 브로커에 같은 전달 계약을 적용한다. | 브로커 왕복 지연, Kafka 인프라·저장 공간, consumer 복구 설계가 필요하다. | 채택 방향 |
| HTTP 요청에서 DB 처리 완료 후 응답 | 응답과 DB 결과의 관계가 단순하고 별도 전달 인프라가 없다. | 응답이 전체 계산·DB 처리 시간에 종속되며 수락과 비동기 처리를 분리하는 목표에 맞지 않는다. | 미채택 |

## 결과와 트레이드오프

- 정상 HTTP 수락 경로가 Kafka 가용성과 발행 ACK 지연에 의존한다. 메모리 수락
  응답과 같은 지연이라고 가정하지 않는다.
- 수락한 원본은 API 프로세스 메모리에만 존재하지 않는다. 보존·장애 내성의 범위는
  연결한 Kafka의 복제·저장·retention 설정에 달려 있다. 로컬 Docker 개발 구성의
  단일 브로커·volume 특성을 운영 환경의 보장으로 일반화하지 않는다.
- 현재 메모리 경로의 Worker 재시도·실패 보관·Device별 보류는 그대로 사용할 수
  있는 Kafka offset 정책이 아니다. 위 미결정을 해소하고 실제 브로커로 검증해야 한다.
- 검증에는 발행 ACK 전후 응답, ACK 미확인 후 동일 배치 재시도, API 재시작,
  DB commit 직후 consumer 종료와 재전달, 실패 Device 보류와 다른 Device 처리
  지속을 포함한다. Kafka producer·consumer가 시작·종료되는 수명도 검증한다.
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
