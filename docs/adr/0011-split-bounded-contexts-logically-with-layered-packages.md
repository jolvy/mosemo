---
status: accepted
date: 2026-11-04
---
# Bounded Context를 논리적으로 분리하고 BC마다 계층 패키지를 둔다

## 맥락

`src/mosemo`는 accounts, auth, devices, activities, focus_sessions, activity_labels,
batch_jobs와 루트 파일들(`dependencies.py`, `schemas.py`, `exceptions.py`, `openapi.py`,
`database.py`, `clock.py`, `timezones.py`)로 이루어진 평평한 기능 모듈 구조다. 모듈 사이의
규칙이 없어 다음 문제가 생겼다(`95abb4b` 기준 측정).

- 패키지 단위 import 순환이 3쌍 있다. activities↔focus_sessions, activities↔infra,
  activity_labels↔batch_jobs.
- 루트 `dependencies.py`가 모든 모듈의 service·repository를 import하고, 모든 router가 이
  파일을 import한다. 순환의 중심이다.
- 다른 모듈의 엔티티와 repository를 그대로 쓴다. 예를 들어 activity_labels가
  `ActivityTimelineSegment`의 메서드와 `ActivityRepository.lock_timeline`을 직접 호출하고,
  auth가 `LabelCatalogRepository.add_all`로 다른 모듈의 테이블에 쓴다.
- 역할 목록 밖 파일이 35개 있어 파일 배치 규칙을 검사할 수 없다.

곧 활동 분석(앱 사용 시간, 컨텍스트 스위칭, 집중 이탈)이 추가된다. 분석은 관찰 활동 구간,
확정 라벨, 집중 세션 세 원천을 결합하고 결과를 따로 저장한다. 지금 구조에서는 이 기능이
어느 모듈에 놓이든 다른 모듈들을 끌어온다.

[용어집](../ubiquitous-language.md)은 이미 「계정과 Device」, 「활동 관찰」, 「활동 해석」
섹션으로 업무 언어를 나누고 있다. 팀은 하나이고 DB도 하나이며, 서버를 운영하지 않는 기간이라
구조 변경 비용이 지금 가장 작다.

선택 기준은 다음과 같다.

- 모듈 사이 의존이 한 방향이고 순환이 없는가
- 한 업무 영역의 모델이 다른 영역의 변경에 끌려가지 않는가
- 경계를 넘는 원자적 처리(가입 시 기본 라벨 생성, 라벨 확정·제안 중 타임라인 잠금)를 지금처럼
  유지할 수 있는가
- 규칙을 도구로 강제할 수 있는가
- 이후 데이터 분리나 서비스 분리로 넓힐 여지가 있는가

## 결정

### 분리 수준: 논리 분리(L1)

- 프로세스, DB, Postgres 스키마는 하나로 유지한다. BC 사이의 외래키와 한 트랜잭션 안의
  BC 간 호출도 유지한다.
- 다른 BC의 코드는 그 BC의 `public.py`로만 사용한다. 엔티티를 공유하지 않으며, 다른 BC의
  aggregate는 ID로 참조하고 필요한 데이터는 공개 읽기 모델로 받는다.
- 다른 BC의 테이블과 SQL 조인하지 않고, 다른 BC의 테이블을 직접 조회하지도 않는다. 여러 BC의
  데이터가 필요하면 애플리케이션에서 조립한다(자기 BC를 조회하고, 다른 BC의 공개 클래스를
  호출해 합친다). 예외는 두지 않는다. 성능 때문에 조인이 꼭 필요한 경우가 생기면 이 ADR을 고쳐
  예외 방식을 정한다.

### Bounded Context 5개

| BC | 내용 | 용어집 섹션 |
| --- | --- | --- |
| `identity` | 계정, 로그인·토큰, Device | 계정과 Device |
| `labels` | 계정별 라벨 목록(기본 라벨 생성, 사용 중·보관, 활성 확인) | 활동 해석의 라벨 |
| `observation` | 활동 레코드, 관찰 타임라인 투영과 조회, 집중 세션 | 활동 관찰 |
| `labeling` | 라벨 제안, 확정 라벨, 라벨 타임라인(확정 작업 화면) | 활동 해석 |
| `analytics` | 활동 분석 결과와 갱신 job | 활동 분석(추가 예정) |

- 집중 세션은 observation에 둔다. 세션 라벨은 관찰 구간에 대한 해석이 아니라 사용자가 남긴
  기록이고, 세션 연결 활동의 시간 범위 검증이 활동 기록과 같은 트랜잭션에서 세션을 잠근다.
  labeling은 집중 세션을 읽지 않는다.
- 라벨 목록은 labeling에서 분리한다. 집중 세션 완료가 라벨의 활성 여부를 확인하므로, 라벨
  목록이 labeling에 있으면 observation과 labeling이 서로를 import한다.
- 라벨 타임라인은 확정 응답이 같은 그룹 계산을 다시 쓰는 작업 화면이라 labeling에 둔다.
  여러 원천을 합친 집계 화면은 analytics에 둔다.

### Context Map: BC 간 의존 방향

```text
analytics   ─▶ labeling, observation, labels, identity
labeling    ─▶ observation, labels, identity
observation ─▶ labels, identity
labels      ─▶ identity        (AccountCreated 구독: 가입 시 기본 라벨 생성)
identity    ─▶ (없음)
```

- 순환은 허용하지 않는다. 상류 BC가 하류 BC의 변경을 알아야 할 때는 import하지 않고
  [ADR 0012](0012-notify-cross-context-changes-with-in-process-domain-events.md)의 도메인
  이벤트로 알린다. 가입 시 기본 라벨 생성이 그 예다. identity가 계정을 만들며
  `AccountCreated`를 기록하고, labels가 같은 트랜잭션에서 구독해 기본 라벨을 만든다.
- 하류 BC가 같은 트랜잭션 안에서 상류 BC의 결과를 받아야 하는 동기 호출은 상류 BC의 공개
  협력자([ADR 0013](0013-persist-dataclass-aggregates-with-imperative-mapping.md))로 한다.
  집중 세션 완료의 라벨 활성 확인, 활동 기록의 기기 소유 확인, 라벨 확정과 라벨 제안의
  타임라인 잠금과 구간 조회가 해당한다.
- 라벨 확정과 라벨 제안(선점·저장)은 자기 규칙(확정·제안은 현재 구간 버전에만 적용)을 지키려고
  observation의 계정 타임라인 잠금을 잡는다. L1에서는 유지하지만, 다른 BC의 잠금에 기대는 곳은
  이 둘뿐이므로 데이터 분리(L2)로 갈 때 저장된 `segment_version`을 쓰는 낙관적 검사로 가장 먼저
  바꾼다.
- 현재 계정 provider는 액세스 토큰의 서명과 만료만 검증하고 DB를 조회하지 않는다. 계정이
  있어야 하는 UseCase(계정 조회, 라벨 목록, 계정 시간대를 읽는 조회)가 identity의 공개
  협력자로 계정을 확인한다.

### 패키지 구조

```text
src/mosemo/
├── {bc}/
│   ├── public.py          # 다른 BC가 쓸 수 있는 것: 공개 클래스, 읽기 모델, 이벤트, 오류 코드
│   ├── bootstrap.py       # 조립 루트가 쓰는 것: router, 테이블 매핑, 이벤트 handler 등록, 객체 그래프 생성
│   ├── presentation/      # router, 요청·응답 스키마, 메시지 스키마, FastAPI provider
│   ├── application/       # UseCase, 외부 시스템 포트(ports.py)
│   ├── domain/            # aggregate, 값 객체, 도메인 이벤트, 순수 규칙
│   └── infrastructure/    # Table 정의와 매핑, Repository, Query, 외부 클라이언트
├── shared/                # 공용 커널
├── infra/                 # 기술 요소: DB 엔진·세션, 설정, 로깅, 메시징, 스케줄링 틀, provider
├── api/                   # 조립 루트: FastAPI 앱, 미들웨어, 예외 handler, 공개 OpenAPI
├── consumer/              # 조립 루트: Kafka Consumer 진입점
└── batch/                 # 조립 루트: 배치 job 등록과 진입점
```

- BC가 곧 모듈이다. 계층 폴더는 BC마다 한 벌만 두고, BC 안의 개념(활동, 집중 세션 등)은
  계층 안의 파일로 나눈다. 한 개념의 파일이 많아지면 그 계층 안에서만 하위 폴더를 만든다.
- BC 루트에는 `public.py`, `bootstrap.py`, `__init__.py`만 둔다. `__init__.py`는 비워 두고
  docstring만 허용한다. `__init__.py`는 `protected` 대상이 아니므로, 여기서 내부 계층을 다시
  내보내면 다른 BC가 `public.py`를 거치지 않고 내부 객체를 import할 수 있다.
- 계층 의존은 presentation → application → infrastructure → domain 방향으로만 둔다.
  presentation은 infrastructure를 건너뛰어 import하지 않는다.
- 포트(`Protocol`)는 외부 시스템 경계(OAuth 공급자, AI 제안기, Kafka 발행)에만
  `application/ports.py`에 둔다. Repository와 Query에는 포트를 두지 않는다.
- `shared`에는 둘 이상의 BC가 실제로 쓰고 특정 BC의 업무 의미에 묶이지 않는 것만 둔다.
  엔티티, repository, 업무 규칙은 두지 않는다. `shared`도 BC와 같은 계층으로 나눈다.

  ```text
  shared/
  ├── domain/        AggregateRoot, DomainEvent, Timezone, 날짜 창 계산
  ├── application/   UseCase, Clock, EventDispatcher·Phase·Handler
  └── presentation/  요청·응답 기반 모델, TimelineTimestamp, 오류 틀(ErrorSpec, ApiErrorCode, CommonErrorCode, ApiException), 오류 응답 builder, OpenAPI 오류 helper, 현재 계정 provider 선언
  ```

  BC의 각 계층은 `shared`의 같은 계층이나 그보다 안쪽 계층만 import한다. domain은
  `shared.domain`만, application과 infrastructure는 `shared.domain`·`shared.application`만,
  presentation은 모두 쓸 수 있다.
- 루트 `dependencies.py`는 없앤다.

### 조립

- 각 BC가 자기 객체를 조립한다. `bootstrap.py`는 router, 테이블 매핑 함수, 이벤트 handler
  등록 함수, 상태 없는 UseCase·Repository·Query 객체 그래프를 제공한다. 이 객체들은 요청마다
  만들지 않고 앱 시작 때 한 번 만들며, API·Consumer·batch가 같은 조립을 공유한다.
- 조립 루트(`api`, `consumer`, `batch`)는 BC의 `bootstrap.py`와 `public.py`만 import해 이어
  붙인다. BC의 내부 계층을 직접 import하지 않는다. Alembic 환경(`migrations/env.py`)도 테이블
  메타데이터를 얻기 위해 `bootstrap.py`를 import할 수 있다.
- 라우터는 presentation의 router 생성 함수가 만든다. `bootstrap.py`가 조립한 UseCase를 이
  함수에 넘겨 router를 얻는다. presentation은 `bootstrap.py`를 import하지 않는다.
- 다른 BC의 공개 클래스가 필요하면 그 BC의 `bootstrap.py`가 `public.py`의 클래스를 직접
  만든다.
- DI 컨테이너(dependency-injector 등)는 도입하지 않는다. FastAPI `Depends`는 요청 단위 값
  (DB 세션, 현재 계정)과 조립된 객체를 라우터에 넘기는 데만 쓴다.
- DB 세션, 시계, 설정, HTTP 클라이언트, 이벤트 디스패처의 provider는 `infra.providers`에
  두고 BC가 직접 import한다. 트랜잭션 헬퍼(`unit_of_work`, `read_snapshot`, `record_event`)는
  `infra.transactions`에 둔다. BC가 import할 수 있는 `infra` 모듈은 계층마다 정해 둔다.

  | BC 안의 위치 | import할 수 있는 `infra` 모듈 |
  | --- | --- |
  | presentation | `infra.providers`(FastAPI provider) |
  | application | `infra.transactions` |
  | infrastructure | `infra.persistence`(매핑 registry, 이름 규칙이 붙은 `MetaData`, `TimezoneStorage` 같은 컬럼 타입) |
  | domain | 없음 |
  | `bootstrap.py` | 위 셋과 시계·설정 구현(`infra.clock`, `infra.config`), 배치 job 정의용 스케줄링 틀(`infra.scheduling`의 `BatchJob`, `Schedule`) |

  `infra` 모듈 자체에는 fastapi 사용 제한이 없다. fastapi 금지는 BC와 `shared`의 domain·
  application에만 둔다(강제 방법 참고).
- `faststream`·`aiokafka`는 `infra` 안에만 둔다. `infra`의 메시징은 업무 메시지를 모르는
  어댑터(토픽·키·bytes 발행, 원시 메시지 구독, 런타임 수명 관리)만 제공한다. 메시지 스키마와
  직렬화는 observation이 소유하고, 발행 포트(`ActivityBatchPublisher`)는 observation
  `application/ports.py`에 둔다. 포트 구현과 "구독 메시지 → UseCase" 연결은 `infra` 어댑터와
  observation `public.py`를 함께 쓸 수 있는 조립 루트(`api`, `consumer`)가 한다.
- 현재 계정 provider는 `shared`에 선언하고, 조립 루트가 identity의 토큰 검증 구현을
  `dependency_overrides`로 주입한다.

### 현재 모듈의 이동

| 현재 | 이동 |
| --- | --- |
| `accounts`, `auth`, `devices` | `identity` |
| `activity_labels/catalog`, `LabelResponse` | `labels`(기본 라벨 생성은 `AccountCreated` handler로) |
| `activities`(`consumer.py`, `handler.py` 제외), `focus_sessions` | `observation` |
| `activity_labels`(catalog 제외) | `labeling` |
| `activities/messages.py` | observation(메시지 스키마와 직렬화) |
| `activities/consumer.py` | `consumer` 조립 루트 |
| `activities/handler.py` | faststream 구독 등록과 ack 처리는 `infra` 원시 구독 어댑터, 메시지 해석과 UseCase 호출은 `consumer` 조립 루트 |
| `infra/batch_delivery.py`, `infra/batch_consumer.py`, `infra/batch_runtime.py`, `infra/kafka_compat.py` | 업무 메시지를 모르는 부분은 `infra` 메시징 어댑터, 활동 메시지·executor·미들웨어를 다루는 부분은 `api`·`consumer` 조립 루트 |
| `batch_jobs/job.py`·`server.py` | `infra.scheduling`(apscheduler는 이 모듈만 import). `Schedule`은 apscheduler 트리거를 받지 않고 `infra.scheduling`이 제공하는 생성 함수(예: 주기 지정)로 만들며, ADR 0010의 트리거 시간대(UTC) 규칙은 이 생성 함수 안에서 지킨다. job 정의는 apscheduler를 import하지 않는다 |
| `batch_jobs/registry.py`·`__main__.py`, `activity_labels/proposals/job.py` | `batch` 조립 루트, job 정의는 `labeling.bootstrap` |
| 루트 `dependencies.py` | BC별 `presentation`과 `bootstrap.py`, `infra.providers`로 해체 |
| 루트 `main.py`, `api.py`, `middleware.py`(활동 배치 본문 크기 제한 포함), `exception_handlers.py` | `api` |
| 루트 `schemas.py`, `exceptions.py`, `responses.py`, `openapi.py` | `shared.presentation`(오류 코드는 [ADR 0014](0014-align-public-identifiers-and-error-codes-with-contexts.md)대로 BC별로 나누고, `public_openapi()`는 `api`로) |
| 루트 `timezones.py` | `Timezone`은 `shared.domain`, SQLAlchemy `TimezoneStorage`는 `infra.persistence` |
| 루트 `database.py` | 엔진·세션 팩토리는 `infra`, 매핑 registry·`MetaData`는 `infra.persistence` |
| 루트 `config.py`, `logging.py` | `infra` |
| 루트 `clock.py` | `Clock` 타입은 `shared.application`, `utc_now` 구현은 `infra.clock` |

실행 명령(`poe batch-consumer`, `poe batch-jobs`)과 배포 매니페스트의 실행 모듈 경로는
조립 루트 이동에 맞춰 바꾼다.

### 강제 방법

import-linter 계약으로 강제한다.

| 규칙 | 계약과 옵션 |
| --- | --- |
| BC 간 방향(analytics > labeling > observation > labels > identity) | `layers` |
| BC 내부 계층(presentation > application > infrastructure > domain) | `layers` + `containers` |
| presentation → infrastructure 직접 import 금지 | `forbidden`, `allow_indirect_imports = true`(application을 거치는 경로는 허용) |
| BC 내부 계층은 같은 BC만 import | BC마다 `protected`(내부 계층 4개, 허용 importer는 그 BC) |
| `bootstrap.py`는 조립 루트만 import | BC마다 `protected`(Alembic 환경은 `mosemo` 밖이라 계약 대상이 아님) |
| `shared`, `infra`는 BC와 조립 루트를 import하지 않음 | `forbidden` |
| BC의 각 계층은 위 표의 `infra` 모듈만 직접 import | 계층마다 `forbidden`(허용 외 `infra` 모듈 열거), `allow_indirect_imports = true` |
| BC와 `shared`의 domain은 `sqlalchemy`, `fastapi`, `pydantic`을 import하지 않음 | `forbidden`, `include_external_packages = true`, 간접 import도 검사 |
| BC와 `shared`의 application은 `fastapi`를 import하지 않음 | `forbidden`, `include_external_packages = true`, `allow_indirect_imports = true`(직접 import만 검사) |
| `shared` 내부 계층(presentation > application > domain) | `layers` |
| BC domain은 `shared.application`·`shared.presentation`을, BC application·infrastructure는 `shared.presentation`을 import하지 않음 | `forbidden` |
| `faststream`, `aiokafka`는 `infra`만 import | `forbidden`(BC·`shared`·조립 루트 대상), `include_external_packages = true`, `allow_indirect_imports = true` |
| `apscheduler`는 `infra.scheduling`만 import | `forbidden`(BC·`shared`·조립 루트 대상), `include_external_packages = true`, `allow_indirect_imports = true` |

AST 검사기로는 BC 루트 파일 제한, BC 루트 `__init__.py`에 docstring 외 문장이 없는지,
`Protocol`의 `application/ports.py` 위치를 검사한다. 다른 BC 테이블 조회 금지는 BC 내부 계층
`protected` 계약이 막는다(다른 BC의 `Table`·매핑은 그 BC의 infrastructure에만 있다).
조립 루트 테스트로 모든 BC 라우터가 현재 계정 provider를 받는지(공개 엔드포인트 예외
목록 제외)와 override 누락이 없는지 확인한다. 검사 명령은 `poe`와 CI, AGENTS.md 검증
목록에 추가한다. 계약은 리팩터링 중 각 BC를 옮기는 작업에서 그 BC의 계약과 함께 추가한다.

리뷰로 확인할 것: 새 BC나 BC 이동이 용어집 섹션과 맞는가, 어떤 BC가 다른 BC의 테이블 이름으로
`Table`을 따로 정의하지 않았는가(import가 없어 계약이 잡지 못한다), `shared`에 들어가는 것이 정말
여러 BC가 쓰는 업무 무관 요소인가.

## 고려한 대안

| 대안 | 장점 | 단점·비용 |
| --- | --- | --- |
| 현상 유지(평평한 기능 모듈 + 모듈 간 규칙만 추가) | 이동 비용이 없다. | 순환 3쌍과 루트 `dependencies.py`가 남고, analytics가 들어오면 다시 순환이 생긴다. 모듈 사이 엔티티 공유를 막을 경계가 없다. |
| 데이터 분리(L2: BC별 스키마, 교차 FK·조인 제거) | BC 독립성이 커지고 서비스 분리로 이어가기 쉽다. | 가입 시 기본 라벨 생성, 라벨 확정·제안 중 타임라인 잠금, 기기 소유 확인을 결과적 일관성·보상 처리·낙관적 검사로 바꿔야 한다. 팀 하나·DB 하나에서 이 비용을 정당화할 문제가 없다. |
| 물리 분리(L3: 서비스 분리) | 배포·확장을 BC별로 할 수 있다. | L2의 비용에 네트워크 호출과 배포 단위가 더해진다. |
| 최상위를 계층으로 나눔(`domain/`, `application/`, `infra/` 아래 기능별) | 계층 규칙이 단순하다. | 한 기능의 코드가 최상위 폴더 여러 곳에 흩어지고 BC 경계를 표현할 수 없다. |
| BC 안의 모듈마다 계층 폴더(BC 하나에 application 여러 벌) | 개념별 코드가 한 폴더에 모인다. | BC 공용 파일(`public.py`, 포트)이 어느 계층에도 속하지 못하고, BC 경계 규칙과 모듈 경계 규칙이 두 겹이 된다. |
| 헥사고날 원형(인바운드·아웃바운드 포트 전부) | 모든 경계가 인터페이스로 분리된다. | Repository와 UseCase마다 포트가 생겨 구현이 하나뿐인 인터페이스가 늘어난다. |
| 조립 루트가 모든 BC 내부를 직접 조립 | 조립 코드가 한곳에 모인다. | 지금의 루트 `dependencies.py`와 같은 순환 중심이 다시 생기고, BC 내부 보호 계약에 예외가 필요하다. |
| DI 컨테이너(dependency-injector) | API·Consumer·batch가 컨테이너 하나를 공유한다. | FastAPI `Depends`와 DI 체계가 둘이 되고, `Provide[...]` 표기는 타입 검사가 약하다. 상태 없는 객체를 `bootstrap.py`에서 한 번 만들면 공유 문제는 이미 풀린다. |
| 읽는 쪽 BC가 다른 BC 테이블의 읽기 전용 정의를 두고 조인 허용 | SQL 한 번으로 거르고 페이지를 셀 수 있다. | 별도 `MetaData`, 검사 규칙이 필요하고 소유 BC의 컬럼 변경을 정적으로 따라가지 않는다. 지금 후보였던 확정 이력 예시 조회는 애플리케이션에서 걸러도 결과가 같다. |

## 결과와 트레이드오프

- 패키지 단위 순환이 없어지고, BC 경계와 계층 방향을 import-linter로 검사할 수 있다.
- 모든 소스 파일을 옮기는 큰 변경이다. BC 단위 하위 작업으로 나누고, 각 작업이 그 BC의
  계약을 추가하면서 진행한다. 옮기는 동안에는 import 경로가 바뀌어 다른 진행 중인 브랜치와
  충돌한다.
- DB와 트랜잭션은 그대로라서 기존 원자적 처리(가입과 기본 라벨, 확정·제안과 타임라인 잠금)가
  유지된다. 대신 BC는 같은 DB 스키마를 공유하므로 L2 수준의 독립성은 없다. 나중에 L2로 갈
  때는 교차 FK와 BC 간 동기 호출을 끊으면 되고, `public.py` 경계와 ID 참조는 그대로 쓸 수
  있다.
- `public.py`의 읽기 모델과 BC별 모델 때문에 비슷한 형태의 클래스가 BC마다 생길 수 있다.
- 조립 루트 이동으로 Consumer와 batch의 실행 모듈 경로가 바뀐다.
- 현재 계정 provider가 계정을 조회하지 않으므로, 발급된 토큰은 만료 전까지 계정 상태와 무관하게
  유효하다. 지금은 계정 삭제·차단 경로가 없어 문제가 되지 않는다. 그런 경로를 만들 때는 토큰
  무효화 방법을 함께 설계한다.

## 대체한 ADR

- [ADR 0010](0010-run-batch-jobs-in-apscheduler-process.md)을 부분 대체한다. "배치 작업은
  `mosemo/batch_jobs` 패키지에 둔다"와 실행 모듈 경로(`python -m mosemo.batch_jobs`)를 이 ADR의
  `infra.scheduling`, `batch` 조립 루트, 각 BC의 `bootstrap.py`로 바꾼다. 또 `Schedule`이
  APScheduler trigger를 직접 받는다는 정의를 `infra.scheduling`의 생성 함수로 만드는 것으로
  바꾸며, UTC 트리거 규칙은 그 함수가 지킨다. job 정의와 실행
  방식의 분리, APScheduler 단일 프로세스, 겹침 방지와 misfire 정책은 ADR 0010을 그대로 따른다.

## 연관 PRD·ADR

- [ADR 0004](0004-project-observation-timeline-on-write.md): 관찰 타임라인 투영은 observation
  안에서 그대로 쓰기 시 갱신한다.
- [ADR 0005](0005-separate-observation-and-personal-label-confirmation.md): 관찰과 라벨 확정의
  분리가 observation과 labeling의 경계가 된다.
- [ADR 0006](0006-deliver-activity-batches-through-kafka.md): Kafka 구독 등록은 `consumer`
  조립 루트와 `infra` 메시징으로 옮긴다.
- [ADR 0010](0010-run-batch-jobs-in-apscheduler-process.md): job 정의는 각 BC의
  `bootstrap.py`가 제공하고, 스케줄링 틀은 `infra`, 등록은 `batch` 조립 루트가 한다.
- [ADR 0012](0012-notify-cross-context-changes-with-in-process-domain-events.md)
- [ADR 0013](0013-persist-dataclass-aggregates-with-imperative-mapping.md)
- [ADR 0014](0014-align-public-identifiers-and-error-codes-with-contexts.md)
- [도메인 용어집](../ubiquitous-language.md)
- [활동 라벨 패키지 재배치 PRD](../prd/activity-label-package-structure.md): 관찰·라벨 목록·확정·제안을
  하나의 바운디드 컨텍스트로 보고 별도 BC 분리를 비목표로 둔 이전 결정이다. 이 ADR이 그 전제를
  바꾼다. 내부 책임 구분(목록, 확정, 제안, 조회)은 labels와 labeling의 파일 구분으로 이어진다.
  그 PRD가 비목표로 둔 메시지 발행과 이벤트 저장소는 이번에도 도입하지 않는다.
