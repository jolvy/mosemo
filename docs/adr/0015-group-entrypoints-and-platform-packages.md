---
status: accepted
date: 2026-11-05
---
# 진입점을 entrypoints로 묶고 기술 기반을 platform으로 재구성한다

## 맥락

[ADR 0011](0011-split-bounded-contexts-logically-with-layered-packages.md)로 `src/mosemo`를 BC로
나눈 뒤 최상위에는 업무 BC(`identity`, `labels`, `observation`, `labeling`), 공용 커널(`shared`),
기술 요소(`infra`), 조립 루트(`api`, `consumer`, `batch`)가 같은 깊이로 놓였다. 다음 문제가 남았다.

- 세 갈래(업무 BC, 실행 기반, 조립 루트)의 역할이 폴더 구조에 드러나지 않는다.
- 최상위 `infra`와 BC 안의 `infrastructure/`는 이름이 비슷하지만 역할이 다르다. 앞의 것은 누구
  소유도 아닌 실행 기반이고, 뒤의 것은 그 BC 전용 테이블·Repository다.
- `infra` 안에서 DB 모듈(`database`, `persistence`, `transactions`)이 흩어져 있고, 메시징에서만
  쓰는 `kafka_compat`이 `messaging/` 밖에 있다.
- BC가 조립 루트를 import하지 못하게 막는 계약이 없다. `shared`·`infra` → 조립 루트 금지만 있다.

후속 작업(#172)에서 `Config`를 설정 소유자별로 나눈다. 그 설정 소유 규칙도 같은 패키지 경계에
기대므로 이 ADR에 함께 적는다. 서버를 운영하지 않는 기간이라 경로를 한 번에 바꾸는 비용이 작다.

선택 기준은 다음과 같다.

- 폴더만 보고 업무 BC, 실행 기반, 조립 루트를 구분할 수 있는가
- BC의 `infrastructure/`와 이름이 혼동되지 않는가
- 새 외부 시스템이나 새 실행 프로세스를 넣을 자리가 정해져 있는가
- BC 계층별 실행 기반 import 허용 범위를 지금과 같게 유지하고 도구로 강제할 수 있는가

## 결정

### 패키지 구조 (이 ADR이 병합될 때 적용)

```text
src/mosemo/
├── identity/ labels/ observation/ labeling/   # BC (ADR 0011 그대로)
├── shared/                                    # 공용 커널 (ADR 0011 그대로)
├── platform/                                  # 누구 소유도 아닌 실행 기반
│   ├── config.py  clock.py  logging.py  logging.json  providers.py  scheduling.py
│   ├── database/
│   │   ├── engine.py          # DB 엔진·세션 팩토리·세션
│   │   ├── persistence.py     # 매핑 registry, 이름 규칙 MetaData, 컬럼 타입
│   │   └── transactions.py    # unit_of_work, read_snapshot, record_event
│   └── messaging/
│       ├── publisher.py  runtime.py  subscriber.py
│       └── kafka_compat.py
└── entrypoints/                               # 조립 루트, 프로세스마다 하나
    ├── api/
    ├── consumer/
    └── batch/
```

- 조립 루트 `api`, `consumer`, `batch`를 `entrypoints/` 아래로 옮긴다. 새 실행 프로세스는
  `entrypoints/` 아래 프로세스 이름의 패키지로 추가한다.
- `infra`를 `platform`으로 바꾼다. `platform` 안은 외부 시스템별로 묶는다. DB 모듈은
  `platform/database/`, Kafka 모듈은 `platform/messaging/`에 두고, 특정 외부 시스템에 속하지 않는
  모듈은 `platform` 루트에 둔다. 새 외부 시스템(예: 캐시)은 `platform/` 아래 시스템 이름의 하위
  패키지로 추가한다. 쓰기 전에 미리 만들지 않는다.
- 옛 경로를 남기는 호환 re-export 모듈은 두지 않는다.

이전 경로와의 대응은 다음과 같다.

| 이전 | 이후 |
| --- | --- |
| `mosemo.infra.{config,clock,logging,providers,scheduling}` | `mosemo.platform.{config,clock,logging,providers,scheduling}` |
| `mosemo.infra.database` | `mosemo.platform.database.engine` |
| `mosemo.infra.persistence` | `mosemo.platform.database.persistence` |
| `mosemo.infra.transactions` | `mosemo.platform.database.transactions` |
| `mosemo.infra.kafka_compat` | `mosemo.platform.messaging.kafka_compat` |
| `mosemo.infra.messaging.*` | `mosemo.platform.messaging.*` |
| `mosemo.api.*`, `mosemo.consumer.*`, `mosemo.batch.*` | `mosemo.entrypoints.api.*`, `mosemo.entrypoints.consumer.*`, `mosemo.entrypoints.batch.*` |

실행 명령은 `python -m mosemo.entrypoints.consumer`, `python -m mosemo.entrypoints.batch
{serve | run JOB_NAME}`, FastAPI 앱 `mosemo.entrypoints.api.main:app`이다. poe 작업 이름
(`dev`, `batch-jobs`, `batch-consumer`)은 바꾸지 않는다.

### import 경계 (이 ADR이 병합될 때 적용)

BC 계층별로 import할 수 있는 `platform` 모듈은 ADR 0011의 `infra` 허용 범위와 같다.
`platform.database`가 패키지이므로 허용과 금지는 모듈 단위로 적는다.

| BC 안의 위치 | import할 수 있는 `platform` 모듈 |
| --- | --- |
| presentation | `platform.providers` |
| application | `platform.database.transactions` |
| infrastructure | `platform.database.persistence` |
| domain | 없음 |
| `bootstrap.py` | 위 셋과 `platform.clock`, `platform.config`, `platform.scheduling` |

- BC 4개 전체(계층, `bootstrap.py`, `public.py` 포함)는 `mosemo.entrypoints`를 직접·간접 모두
  import하지 않는다(`forbidden`, `allow_indirect_imports` 미설정). 새 BC를 추가하면 이 계약의
  대상에도 넣는다.
- `shared`·`platform`은 BC와 `mosemo.entrypoints`를 import하지 않는다.
- BC의 `bootstrap.py`를 import할 수 있는 것은 `mosemo.entrypoints`뿐이다(Alembic 환경은 `mosemo`
  밖이라 계약 대상이 아니다).
- `faststream`·`aiokafka`는 `platform`만, `apscheduler`는 `platform.scheduling`만 import한다.
- 진입점끼리(`api`↔`consumer`↔`batch`)의 import 금지는 이 ADR에서 정하지 않는다.

### 설정 소유 규칙 (후속 설정 분리 작업 #172가 완료될 때 적용)

- `platform`은 DB 설정(`DatabaseSettings`, `DB_*`)만 직접 읽는다. Kafka 설정 모델
  (`BatchDeliveryConfig`)은 `platform/messaging/`에 두고 직접 읽지 않는다.
- 각 진입점(`api`, `consumer`, `batch`)은 자기 프로세스가 쓰는 환경 변수만 읽는 설정 클래스를 하나씩
  두고, 시작할 때 DB 설정과 함께 검증한다. 환경 변수 이름은 바꾸지 않는다.
- BC는 환경 변수나 설정 클래스를 읽지 않는다. 필요한 값은 BC `bootstrap.py`가 정의한 순수 타입
  (identity의 `IdentityConfig`)으로 조립 루트가 시작할 때 생성자에 넘기고, FastAPI `Depends`로
  설정을 받지 않는다. 환경 이름 같은 배포 판단은 진입점이 값으로 바꿔 넘긴다(`secure_cookies`).
- #172가 완료되기 전까지는 `platform/config.py`의 `Config`와 BC `bootstrap.py`의 `platform.config`
  import 허용을 그대로 둔다.

## 고려한 대안

| 대안 | 장점 | 단점·비용 |
| --- | --- | --- |
| 현상 유지(최상위에 BC·`infra`·조립 루트를 나란히 둠) | 이동 비용이 없다. | 역할 구분이 폴더에 드러나지 않고 `infra`/`infrastructure` 혼동이 남는다. BC → 조립 루트 금지 계약도 없다. |
| 조립 루트만 `entrypoints/`로 묶고 `infra` 이름 유지 | 이동 범위가 작다. | `infra`와 BC `infrastructure/`의 혼동이 남는다. |
| `infra`를 `platform`으로 바꾸되 내부는 평평하게 유지 | 이름 혼동은 풀린다. | DB 모듈이 흩어져 있고 `kafka_compat`이 메시징 밖에 남는다. 새 외부 시스템의 자리가 정해지지 않는다. |
| `platform` 내부를 계층(어댑터·설정 등)으로 나눔 | 모듈 종류가 드러난다. | 한 외부 시스템의 코드가 여러 폴더로 흩어지고, BC 계층별 허용 모듈을 적기 어려워진다. |
| 옛 경로에 호환 re-export 모듈을 남김 | 저장소 밖 실행 설정을 천천히 바꿀 수 있다. | 운영 중인 서버가 없어 얻는 것이 없고, 옛 경로 import가 다시 퍼질 수 있다. |

## 결과와 트레이드오프

- 최상위 폴더가 업무 BC, `shared`, `platform`, `entrypoints`로 나뉘어 역할이 폴더 이름으로 드러난다.
- BC가 조립 루트에 기대는 import를 `lint-imports`가 막는다.
- 모든 진입점과 `platform` 모듈의 import 경로가 바뀐다. 진행 중인 다른 브랜치와 충돌하고, 저장소
  밖에서 옛 모듈 경로로 실행하는 설정이 있으면 함께 바꿔야 한다.
- `platform`은 표준 라이브러리 모듈 이름과 같다. `mosemo.platform`으로 절대 import하므로 충돌하지
  않지만, `src/mosemo`를 작업 디렉터리로 두고 스크립트를 실행하면 표준 `platform`이 가려질 수 있다.
  저장소의 실행 경로(`python -m mosemo...`, pytest, alembic)는 저장소 루트에서 실행한다.
- 파일 이동 커밋과 내용 변경 커밋을 섞지 않아 rename 감지로 이력을 따라갈 수 있게 한다.

## 대체한 ADR

- [ADR 0011](0011-split-bounded-contexts-logically-with-layered-packages.md)을 부분 대체한다. 바뀌는
  조항은 다음과 같다.
  - 「패키지 구조」의 `infra/`, `api/`, `consumer/`, `batch/` 위치와 이름을 이 ADR의 `platform/`,
    `entrypoints/{api,consumer,batch}/`로 바꾼다.
  - 「조립」의 BC가 import할 수 있는 `infra` 모듈 표를 이 ADR의 `platform` 모듈 표로 바꾼다.
    허용 범위는 같다.
  - 「강제 방법」에 BC → `mosemo.entrypoints` 금지 계약을 더한다.
- BC 구성, Context Map, BC 내부 계층과 그 밖의 조립 규칙은 ADR 0011을 그대로 따른다.

## 연관 PRD·ADR

- [ADR 0006](0006-deliver-activity-batches-through-kafka.md): Kafka 구독 등록은
  `entrypoints/consumer`와 `platform/messaging`이 맡는다.
- [ADR 0010](0010-run-batch-jobs-in-apscheduler-process.md): 스케줄링 틀은 `platform.scheduling`,
  job 등록은 `entrypoints/batch`가 한다.
- [ADR 0011](0011-split-bounded-contexts-logically-with-layered-packages.md)
- [ADR 0013](0013-persist-dataclass-aggregates-with-imperative-mapping.md): 매핑 registry와
  `MetaData`는 `platform.database.persistence`에 둔다.
