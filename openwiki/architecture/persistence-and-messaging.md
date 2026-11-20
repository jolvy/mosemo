---
type: architecture
title: 영속성·메시징·배치 실행
description: dataclass aggregate와 imperative mapping, 트랜잭션 헬퍼와 도메인 이벤트, Alembic 마이그레이션, Kafka 활동 배치 전달과 Consumer, APScheduler 배치 job의 현재 동작을 정리한다.
tags: [persistence, sqlalchemy, domain-events, kafka, batch-jobs, alembic]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-20T10:08:17.353Z
sources:
  - id: openwiki-source-d1449bb9b8fd8b54431d973d
    resource: repo://docs/adr/0004-project-observation-timeline-on-write.md
  - id: openwiki-source-d518fe7f3f6959ce80a5ce9d
    resource: repo://docs/adr/0006-deliver-activity-batches-through-kafka.md
  - id: openwiki-source-b6d3db16ac14391f66e21084
    resource: repo://docs/adr/0007-store-batch-receipts-with-activity-transactions.md
  - id: openwiki-source-a8f4222bf516e7cad0ac1023
    resource: repo://docs/adr/0008-order-observations-and-transition-existing-timelines.md
  - id: openwiki-source-a41ded727f97fd17b9db4917
    resource: repo://migrations/env.py
  - id: openwiki-source-9f8f1a4a29c395f1cf125a86
    resource: repo://migrations/versions/2026-11-15_0001_c5d6e7f8a9b0_create_analytics_tables.py
  - id: openwiki-source-41b37a3a7fc5c266286c3c46
    resource: repo://src/mosemo/analytics/application/marking.py
  - id: openwiki-source-e650be5fbac67192522ee23b
    resource: repo://src/mosemo/analytics/bootstrap.py
  - id: openwiki-source-3442532cd6f9530cce2c8b79
    resource: repo://src/mosemo/entrypoints/batch/config.py
  - id: openwiki-source-b6bd642164e0d8070a6cc270
    resource: repo://src/mosemo/entrypoints/batch/registry.py
  - id: openwiki-source-2b699ac23a30c5760b936ebd
    resource: repo://src/mosemo/entrypoints/consumer/activity_batches.py
  - id: openwiki-source-c9cecb972e94c477c68a5801
    resource: repo://src/mosemo/observation/application/batches.py
  - id: openwiki-source-fb0ec792c4101db3f47d9358
    resource: repo://src/mosemo/observation/domain/activities.py
  - id: openwiki-source-731f34422dbd773b6c36c5ab
    resource: repo://src/mosemo/observation/presentation/activities/router.py
  - id: openwiki-source-5e193d69747cf2f2e6517922
    resource: repo://src/mosemo/platform/database/persistence.py
  - id: openwiki-source-927994764141916779f261b9
    resource: repo://src/mosemo/platform/database/transactions.py
  - id: openwiki-source-25ac83168948cb4014255129
    resource: repo://src/mosemo/platform/messaging/runtime.py
  - id: openwiki-source-59034fdec52925e5ec21fdd5
    resource: repo://src/mosemo/platform/scheduling.py
  - id: openwiki-source-974f1266b8a564e95f09538b
    resource: repo://src/mosemo/shared/application/events.py
  - id: openwiki-source-bf64c7f959111123b762b90b
    resource: repo://tests/integration/platform/test_transactions.py
generated: { by: "claude-code", at: "2026-11-20T10:08:17.353Z" }
---

# 영속성·메시징·배치 실행

이 페이지는 데이터가 어떻게 저장되고, 활동 배치가 어떻게 전달·처리되며, 주기 작업이 어떻게 실행되는지를 정리한다. 패키지 경계는 [현재 구조](current-structure.md)를, 결정 목록은 [유효한 결정](../decisions/active-decisions.md)을 본다. 원본과 다르면 원본(ADR·코드)이 맞다.

## 도메인 모델과 매핑

근거: [ADR 0013](../../docs/adr/0013-persist-dataclass-aggregates-with-imperative-mapping.md)

- 엔티티는 BC `domain`의 `@dataclass(eq=False, repr=False, kw_only=True)` aggregate다. 값 객체·공개 읽기 모델·이벤트는 `@dataclass(frozen=True)`다.
- 기반 클래스는 `shared`의 `AggregateRoot`(이벤트 목록)와 `DomainEvent`뿐이다.
- `Table`과 imperative mapping은 BC `infrastructure`에 둔다. 각 BC `bootstrap.py`의 `map_tables()`가 `platform.database.persistence`의 `MetaData`(`ORM_METADATA`)에 테이블을 등록한다. `DeclarativeBase`·`mapped_column`은 Ruff `TID251`로 금지된다.
- 엔티티 자신의 식별자는 `id`, 다른 엔티티 참조는 `{엔티티}_id`로 짓는다.

## 트랜잭션과 도메인 이벤트

근거: [src/mosemo/platform/database/transactions.py](../../src/mosemo/platform/database/transactions.py), [ADR 0012](../../docs/adr/0012-notify-cross-context-changes-with-in-process-domain-events.md)

트랜잭션은 UseCase만 연다. Repository·Query·공개 협력자는 첫 인자로 받은 `db_session`을 쓰고 트랜잭션을 열지 않는다.

| 헬퍼 | 동작 |
| --- | --- |
| `unit_of_work(db_session, dispatcher)` | 쓰기 트랜잭션을 연다. 본문이 끝나면 flush하고, 추적 중인 aggregate의 이벤트와 `record_event`로 기록한 이벤트를 남은 것이 없을 때까지 dispatch한 뒤 커밋한다 |
| `read_snapshot(db_session)` | `SET TRANSACTION READ ONLY` 트랜잭션을 열고 이벤트를 dispatch하지 않는다 |
| `consistent_snapshot(db_session)` | `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY` 트랜잭션을 연다. 안의 모든 문장이 같은 스냅샷을 읽으므로 여러 조회를 합치는 읽기가 그 사이 커밋된 행을 보지 않는다. 이벤트를 dispatch하지 않는다. analytics의 조회 UseCase(`GetDailyStats`, `GetFocusSessionAnalysis`)가 쓴다([ADR 0016](../../docs/adr/0016-compute-activity-analytics-on-read-and-store-daily-stats.md) 「분석 조회」) |
| `reset_session(db_session)` | 한 세션에서 여러 트랜잭션을 돌 때 로드된 객체와 추적 root를 비운다 |

- 세 트랜잭션 헬퍼 모두 이미 열린 트랜잭션이 있으면 `TransactionAlreadyOpenError`를 낸다.
- `EventDispatcher`는 현재 `BEFORE_COMMIT` 단계만 지원한다. handler는 같은 세션·트랜잭션에서 실행되므로 handler가 실패하면 원래 변경도 롤백된다.
- 상류 BC가 하류 BC의 변경을 알아야 할 때 import 대신 이 이벤트를 쓴다. 예를 들어 API 조립 루트는 `labels_bootstrap.register_handlers(get_event_dispatcher())`로 labels의 handler를 등록한다.
- 세 조립 루트(API·consumer·batch)는 모두 analytics handler를 등록한다. analytics는 다음 이벤트를 구독해 원천 변경과 같은 트랜잭션에서 (계정, 날짜) 다시 계산 표시를 UPSERT한다. 어느 프로세스에서 일어난 변경이든 표시를 잃지 않도록 세 루트 모두에 묶는다(`tests/unit/harness/test_assembly_roots.py`가 검사).

| 이벤트 | 발행 BC | 표시하는 날짜 |
| --- | --- | --- |
| `TimelineChanged` | observation | 바뀐 범위의 날짜, 그 범위와 겹치는 완료 세션의 시작 날짜, 바로 앞 활동 구간의 끝 날짜 |
| `LabelConfirmed` | labeling | 확정 구간이 걸친 날짜와 겹치는 완료 세션의 시작 날짜 |
| `FocusSessionCompleted` | observation | 세션 시작 날짜 |
| `AccountTimezoneChanged` | identity | 저장 행·표시가 있는 날짜와 그 앞뒤 날짜(새 시간대 기준) |

## 계정 advisory 잠금

근거: [src/mosemo/platform/database/persistence.py](../../src/mosemo/platform/database/persistence.py)

- `lock_account(db_session, account_id)`는 `pg_advisory_xact_lock`으로 계정 잠금을 트랜잭션 끝까지 잡는다(같은 트랜잭션에서 재진입 가능). `SET LOCAL lock_timeout = '3s'` 안에 못 잡으면 `AccountLockTimeoutError`를 낸다.
- 잠금 키는 `activity_timeline:{account_id}`의 blake2b 8바이트 해시다. 계정의 모든 원천 쓰기가 이 키로 직렬화되므로 배포 사이에도 키 바이트를 바꾸면 안 된다.
- observation 타임라인 쓰기와 identity의 계정 시간대 변경(`ChangeAccountTimezone`)이 이 잠금을 쓴다. analytics 표시 handler도 타임라인 잠금을 먼저 잡은 뒤 계정 시간대를 읽으므로, 동시에 시간대가 바뀌어도 옛 시간대 날짜에 표시가 남지 않는다.

## 마이그레이션

- Alembic 환경 [migrations/env.py](../../migrations/env.py)는 다섯 BC(identity·labels·observation·labeling·analytics)의 `map_tables()`만 호출해 `ORM_METADATA`를 채운다. 테이블 정의를 따로 import하지 않는다.
- 리비전은 `migrations/versions/`에 날짜 접두어 파일로 쌓인다. 이미 적용된 마이그레이션은 다시 쓰지 않고, 스키마 변경에는 새 마이그레이션을 추가한다([AGENTS.md](../../AGENTS.md) 「변경 원칙」).
- 최근 리비전은 집중 세션 일시중지(`focus_session_pauses` 테이블과 `focus_sessions.pauses_reported`)와 analytics 테이블(`analytics_definitions` 정의 행 version 1, `analytics_daily_markers`, `analytics_daily_stats`, 표시 version 시퀀스)이다. analytics 테이블의 `account_id`는 `accounts.id`를 `ON DELETE CASCADE`로 참조한다.
- 로컬 적용은 `poe migration-dev`(`alembic upgrade head`)다.

## 활동 배치 전달(Kafka)

근거: [ADR 0006](../../docs/adr/0006-deliver-activity-batches-through-kafka.md), [ADR 0007](../../docs/adr/0007-store-batch-receipts-with-activity-transactions.md), [ADR 0008](../../docs/adr/0008-order-observations-and-transition-existing-timelines.md)

```mermaid
sequenceDiagram
  participant App as 클라이언트
  participant API as API 프로세스
  participant K as Kafka
  participant C as Consumer 프로세스
  participant DB as PostgreSQL
  App->>API: POST /api/v1/activities/batches
  API->>K: 발행(acks=all, idempotence)
  K-->>API: ACK
  API-->>App: 202 accepted
  K->>C: 메시지(key=accountId)
  C->>DB: receipt·원본·타임라인을 한 트랜잭션에 커밋
```

- API의 `POST /api/v1/activities/batches`는 인증·입력·Device 귀속 확인과 24시간 수락 기간 검사 뒤 배치를 Kafka에 발행하고, 발행 ACK를 받으면 `202`를 돌려준다. 수락 기간 검사는 모든 원본의 `observedAt`이 `수락 시각 − 24시간 ≤ observedAt ≤ 수락 시각`(양 끝 포함, 절대 시간)인지 보며, 하나라도 벗어나면 발행하지 않고 배치 전체를 422 `ACTIVITY_RECORD_OUTSIDE_ACCEPTANCE_WINDOW`로 거절한다. Device 귀속 404가 먼저다. 직접 처리 경로 `/activities/batches/legacy`도 타임라인 잠금 전에 같은 검사를 하고 아무것도 저장하지 않는다. Consumer는 다시 검사하지 않는다. DB 처리나 Consumer offset commit을 기다리지 않는다. 발행할 수 없거나 ACK가 늦으면 `503`과 `Retry-After: 1`을 돌려주고, 클라이언트는 같은 `batchId`·내용으로 재시도한다.
- Publisher는 `acks="all"`, `enable_idempotence=True`로 만든 `KafkaBroker`를 쓴다([src/mosemo/platform/messaging/runtime.py](../../src/mosemo/platform/messaging/runtime.py)). 메시지 key는 계정 ID라서 같은 계정은 같은 partition으로 모인다.
- 기본 토픽은 `activities.registration-requested`, Consumer group은 `mosemo.activity-batches`다([src/mosemo/platform/messaging/config.py](../../src/mosemo/platform/messaging/config.py)).
- Consumer 진입점(`poe batch-consumer`, [src/mosemo/entrypoints/consumer/activity_batches.py](../../src/mosemo/entrypoints/consumer/activity_batches.py))은 `BATCH_ENABLED=true`가 아니면 시작하지 않는다. 시작할 때 identity·labels·observation·analytics의 테이블을 매핑하고 analytics handler를 등록한 뒤, 메시지마다 새 세션을 열어 observation의 배치 처리 UseCase를 부른다.
- 배치 처리는 receipt claim, 원본별 채택·거절, 원본 저장, 관찰 타임라인 갱신, 처리 결과 저장을 하나의 DB 트랜잭션에서 수행한다. receipt의 식별 범위는 `(accountId, deviceId, batchId)`다(ADR 0007).
- 원본 순서는 같은 Device 안에서는 `sequence`, 여러 Device 사이에서는 `(observedAt, deviceId, sequence, eventId)` 오름차순으로 병합한다(ADR 0008).

## 관찰 타임라인 projection

근거: [ADR 0004](../../docs/adr/0004-project-observation-timeline-on-write.md)

- 활동 원본은 보존하고, 관찰 활동 구간과 수집 공백은 쓰기 시 `activity_timeline_segments` 테이블에 projection한다. 조회는 projection을 다시 계산하지 않는다.
- 늦게 도착한 원본도 받아들이며, 영향받는 구간부터 다시 재생해 기존 구간과 수렴할 때까지만 범위를 넓힌다.
- 날짜 경계는 계정의 IANA 시간대로 계산한다.
- ADR 0004의 쓰기 경계 일부는 ADR 0007이, 원본 순서·이력 전환 일부는 ADR 0008이 부분 대체했다. 대체 관계는 [유효한 결정](../decisions/active-decisions.md)에 정리되어 있다.

## 배치 job(APScheduler)

근거: [ADR 0010](../../docs/adr/0010-run-batch-jobs-in-apscheduler-process.md)(실행 경로는 [ADR 0011](../../docs/adr/0011-split-bounded-contexts-logically-with-layered-packages.md)이 부분 대체), [src/mosemo/platform/scheduling.py](../../src/mosemo/platform/scheduling.py), [src/mosemo/entrypoints/batch/registry.py](../../src/mosemo/entrypoints/batch/registry.py)

- 활동 배치(Kafka)와 구분하려고 이 구조는 "batch job"으로 부른다.
- 모든 job은 `entrypoints/batch/registry.py`의 `build_jobs`에 등록하고 이름이 유일해야 한다. `build_jobs`는 job을 만들기 전에 다섯 BC의 테이블을 매핑하고 analytics handler를 등록한다. 현재 등록된 job은 세 개다.

| job | 스케줄 | 하는 일 |
| --- | --- | --- |
| 라벨 제안 job(labeling) | 있음 | AI 라벨 제안 |
| `analytics-daily-stats` | 5분마다, 시작 즉시 1회 | 끝난 날짜의 다시 계산 표시를 선점해 일간 통계를 저장 |
| `analytics-definition-mark` | 없음(`run`으로 손으로 실행) | 정의 version이 바뀐 뒤 옛 정의로 저장된 날짜에 정의 재처리 표시를 씀 |

- 실행 방식은 두 가지다: `poe batch-jobs serve`(스케줄 있는 job을 주기 실행하는 상시 프로세스)와 `poe batch-jobs run JOB_NAME`(한 번 실행하고 결과를 exit 코드로 알림).
- batch 설정([src/mosemo/entrypoints/batch/config.py](../../src/mosemo/entrypoints/batch/config.py))은 `LABEL_MODEL`과 `ANALYTICS_REPROCESS_PER_RUN`(기본 600, 0이면 정의 재처리 중지, 음수 거절)을 읽는다. `ANALYTICS_TEST_WAIT_*`는 테스트 전용이다.
- job은 `JobReport`(로그에 붙일 카운터 `fields`, 예외 없이 실패한 실행을 알리는 `failed`)를 돌려줄 수 있다. 카운터는 결과 로그 한 줄(`batch job completed`/`batch job failed`)에 더해지고, `failed=True`면 실패 로그를 남기고 실패로 판정한다.
- 스케줄러는 UTC `AsyncIOScheduler`이며 모든 job에 `max_instances=1`, `coalesce=True`, `misfire_grace_time=None`을 적용한다. 종료할 때는 실행 중 job을 취소하고 최대 10초 기다린다.
- 상시 프로세스는 단일 인스턴스를 가정하며 분산 잠금이 없다.

## 관련 페이지

- [현재 구조](current-structure.md)
- [유효한 결정](../decisions/active-decisions.md)
- [AWS EKS 랩과 측정 세션](../operations/aws-eks-lab.md)
