---
status: accepted
date: 2026-10-24
---
# 배치 작업은 job 정의와 실행 방식을 분리하고 APScheduler 기반 단일 프로세스에서 주기 실행한다

## 맥락

라벨 제안은 별도 polling 작업자(`python -m mosemo.activity_labels.proposals.worker`,
`poe label-worker`)가 10초마다 `ProposalScanner.run_batch()`를 호출해 만든다. 이 루프는
라벨 제안 하나만을 위해 만든 것이라, 앞으로 생길 배치 작업은 각자 진입점과 루프를 따로
만들어야 한다. 라벨 제안은 5분 주기로 바꾸고, 다른 배치 작업도 한 구조에 job으로 추가할
수 있어야 한다. 이후 작업에는 "매일 03:00 집계"처럼 벽시계 기준 스케줄이 필요할 수 있다.

배치 작업이 모두 주기 실행은 아니다. 장애 복구나 재처리를 위해 운영자가 한 번 실행하는
경우가 있고, 앞으로 실시간 이벤트가 job을 실행하는 경우도 생길 수 있다.

선택 기준은 다음과 같다.

- job 정의가 실행 방식(주기, 수동, 이벤트)에 묶이지 않는가
- interval과 cron 스케줄을 모두 표현할 수 있는가
- 같은 job이 겹쳐 실행되지 않는가
- 지연된 실행을 어떻게 처리하는가(misfire, coalesce)
- 의존성과 우리가 유지할 코드는 얼마나 되는가
- 추가 인프라가 필요한가
- 기존 asyncio·SQLAlchemy async 실행 모델과 맞는가

## 결정

- 배치 작업은 `mosemo/batch_jobs` 패키지에 둔다. job은 이름과 인자 없는 async 함수로
  정의하고(`BatchJob`), 주기 실행 설정(`Schedule`: APScheduler trigger와 시작 즉시 실행
  여부 `run_on_start`)은 선택 항목으로 붙인다. 모든 job은 한 레지스트리에 등록하고 이름은
  유일해야 한다. 활동 배치(ADR 0006)와 구분하려고 단독 `batch`라는 이름은 쓰지 않는다.
- 실행 방식은 두 가지다. 모두 같은 실행 래퍼(로그, 예외 격리)를 거친다.
  - `python -m mosemo.batch_jobs serve`(`poe batch-jobs serve`): 스케줄이 있는 job을
    주기 실행하는 상시 프로세스
  - `python -m mosemo.batch_jobs run JOB_NAME`(`poe batch-jobs run JOB_NAME`): 지정한 job을 한 번
    실행하고 결과를 exit 코드로 알리는 일회성 프로세스. 장애 복구와 재처리에 쓴다.
    exit 0은 job 함수가 예외 없이 끝났다는 뜻이며 개별 처리 단위의 성공을 뜻하지 않는다.
- 이벤트 트리거는 이번에 만들지 않는다. 사례가 생기면 이벤트 출처를 정하는 결정을 따로 하고,
  그때 job 정의는 바꾸지 않고 실행 경로만 추가한다.
- 주기 실행에는 `apscheduler==3.11.3`의 `AsyncIOScheduler`를 메모리 jobstore로 사용한다.
  4.0은 pre-release(4.0.0a6)이고 하위 호환이 깨질 수 있어 사용하지 않는다. 등록 옵션
  `max_instances=1`, `coalesce=True`, `misfire_grace_time=None`은 모든 job에 공통으로
  적용한다. 앞 실행이 끝나지 않았으면 그 tick은 건너뛴다.
- 상시 프로세스는 단일 인스턴스로 운영한다고 가정한다. 프로세스 사이의 분산 잠금은 두지
  않는다. 따라서 job은 수동 재실행과 서버 실행과의 동시 실행에 안전해야 한다.
- 종료 신호를 받으면 새 실행을 멈추고, 실행 중인 job은 취소한 뒤 최대 10초 동안
  끝나기를 기다리고 나서 DB engine을 정리한다. job은 취소되어도 다음 실행에서 이어서 처리할 수 있어야 한다.
  취소된 라벨 제안 시도는 lease가 만료된 뒤 다음 실행에서 재시도된다. 이때 시도 1회를 소모하고,
  마지막 시도였다면 실패로 종결된다. 기존 작업자가 강제 종료될 때와 같은 동작이다.
- 라벨 제안 job(`label-proposals`)은 서버가 시작되면 바로 한 번 실행하고, 그 뒤로는 5분
  interval로 실행한다. 실행할 때마다 후보를 끝까지 한 번 훑는다(sweep). `LABEL_MODEL`이
  없거나 형식이 잘못되면 시작 단계에서 실패한다. 기존 `label-worker` 진입점은 제거한다.

## 고려한 대안

- **현상 유지(job별 자체 polling 루프)**: 의존성이 없다. 대신 job마다 진입점, 신호 처리,
  중복 방지를 반복해서 만들어야 하고 cron은 표현할 수 없다.
- **이름을 `scheduler`로 하고 job에 trigger를 필수로 두기**: 처음 제안한 안이다. 주기
  실행만 표현할 수 있어 수동·이벤트 실행이 들어올 자리가 없다.
- **공통 자체 asyncio 루프 + croniter**: 동작을 완전히 통제할 수 있다. 대신 루프, 중복
  방지, misfire, 종료 처리를 직접 구현하고 테스트해야 한다. cron 파서까지 직접 만들면
  DST와 일·요일 OR 규칙 같은 오류가 생기기 쉬워 파서 의존성(croniter 6.2.4)은 필요하다.
- **aiocron 2.1**: 가볍다. 대신 cron만 지원하고, 중복 실행 방지와 misfire 처리는 직접
  구현해야 한다. 마지막 릴리스는 2025-02다.
- **taskiq + taskiq-faststream**: FastStream 생태계와 맞고 분산 실행과 이벤트 기반 실행을
  지원한다. 대신 scheduler 프로세스와 worker 프로세스, 브로커 메시지 경로가 필요하다. 단일
  인스턴스 배치 작업에는 과하다. 이벤트 트리거를 설계할 때 다시 검토할 수 있다.
- **APScheduler 3.11.3(선택)**: interval·cron, `max_instances`, coalesce,
  `misfire_grace_time`을 기본으로 제공하고 브로커가 필요 없다. 대신 타입 정보(py.typed)가
  없고, `AsyncIOExecutor.shutdown()`이 실행 중인 task를 취소만 하고 기다리지 않는다.

## 결과와 트레이드오프

- 새 배치 작업은 도메인 모듈에 job 팩토리를 만들고 레지스트리에 등록하는 것으로 추가한다.
  주기 실행이 필요할 때만 스케줄을 붙인다.
- 스케줄은 프로세스 메모리에만 있다. 재시작하면 interval 기준점이 시작 시각으로
  초기화되고, 중단된 동안 놓친 실행은 따라잡지 않는다. 필요하면 수동 실행으로 보충한다.
- 상시 프로세스를 여러 개 띄우거나 서버 실행 중에 수동 실행하면 같은 job이 동시에 돈다.
  수평 확장이 필요해지면 job별 분산 잠금이나 분산 스케줄러를 다시 결정해야 한다.
- 수동 실행도 레지스트리 전체를 만들므로 모든 job의 필수 설정이 있어야 시작한다.
- 라벨 job의 수동 실행은 지금 처리 대상인 후보만 다음 tick을 기다리지 않고 처리한다.
  최대 시도 횟수를 넘겨 종결된 실패는 되살리지 않으므로, LLM 장애가 길었다면 복구 효과가 작다.
- trigger는 생성할 때의 호스트 로컬 시간대를 쓰므로 항상 `timezone=UTC`로 만든다.
- APScheduler 3에는 타입 정보가 없으므로 그 API 경계는 우리 job 정의 타입으로 감싼다.
- 실행 중인 job을 기다리는 일은 APScheduler에 맡기지 않고 배치 작업 서버에서 직접
  처리한다.

## 대체한 ADR

없음

## 연관 PRD·ADR

- [활동 라벨 패키지 구조 PRD](../prd/activity-label-package-structure.md): AC-1011의
  `poe label-worker` 진입점 유지 조건은 이 결정으로 `poe batch-jobs serve`로 대체된다.
- [ADR 0006](0006-deliver-activity-batches-through-kafka.md): Kafka 활동 배치와는 별개의
  실행 경로다. 혼동을 피하려고 이 구조는 항상 `batch_jobs`/`batch-jobs`로 부른다.
