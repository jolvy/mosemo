# 코드 스타일 평가 기준

> 상태: [프로그램 판정 항목](#프로그램-판정-항목)은 검사기·Ruff·complexipy·import-linter가 강제한다. 강제의 기준은
> 검사기와 설정이고, 이 문서는 항목의 정의와 근거를 기록한다.
> [앞으로 할 일](#앞으로-할-일)의 항목은 아직 강제하지 않는다. 기준 커밋은 `e298365`다.

모델과 무관하게 같은 요구사항에서 같은 형태의 코드가 나오도록, 변경된 코드를 평가하고 기준에
미달하면 다시 개발시키는 데 쓰는 평가 항목을 정의한다.

## 항목 ID

| 형태 | ID | 판정 주체 |
| --- | --- | --- |
| 프로그램 예/아니오 | `CS-P` | 검사기·Ruff·complexipy·import-linter |
| LLM 예/아니오 | `CS-J` | LLM 채점기(앞으로 할 일) |
| LLM 점수 | `CS-S` | LLM 채점기(앞으로 할 일) |

번호는 다시 쓰지 않는다. 폐기한 번호는 `CS-P02`·`CS-P16`과, 파일 이름(`service`·`repository`·`models`·`mappers` 등)으로
역할을 정하던 옛 평평한 패키지용 `CS-P10`·`CS-P25`·`CS-P28`·`CS-P29`·`CS-P30`·`CS-P32`다. 옛 패키지를 모두 Bounded
Context로 옮긴 뒤(#154) 대상 파일이 없어졌고, BC 패키지는
[ADR 0011](adr/0011-split-bounded-contexts-logically-with-layered-packages.md)~[ADR 0014](adr/0014-align-public-identifiers-and-error-codes-with-contexts.md)
「강제 방법」의 import-linter 계약, AST 검사기(`validate_architecture.py`), 내성 테스트로 검사한다.

## 항목 선정 기준

평가 항목은 다음 네 조건을 모두 만족해야 한다.

| 기준 | 판단 질문 |
| --- | --- |
| ① 선택지가 있음 | 같은 요구사항에서 모델이 다르게 쓸 수 있는가? |
| ② 정답이 정해져 있음 | 이 저장소의 ADR·용어집·관례가 정답 하나를 정하는가? |
| ③ 위치를 짚을 수 있음 | 위반을 파일·라인으로 가리킬 수 있는가? |
| ④ 피드백만으로 고칠 수 있음 | 정책 결정 없이 지적만으로 수정할 수 있는가? |

②를 만족하지 못하는 후보는 저장소의 정답을 먼저 정한 뒤 항목으로 추가한다. 도메인 모델 자체를
바꿔야 하는 문제는 ④를 만족하지 않으므로 engineering-planning의 승인 절차로 처리한다.

## 강제 방식

- 도구에 있는 규칙은 도구(Ruff, complexipy, import-linter)로, 없으면 검사기
  (`.codex/skills/verification/scripts/validate_conventions.py`)로 강제한다.
- 모델이 주석이나 설정으로 스스로 피해 갈 수 있는 방식은 쓰지 않는다. 인라인 억제 주석은 금지하고,
  Ruff 설정은 검사기의 고정 목록과 정확히 같아야 하며, complexipy는 고정 명령으로만 실행한다(CS-P01,
  CS-P22).
- 예외는 검사기 안의 허용 목록에 둔다. 목록 변경은 리뷰 대상이다.
- 모든 항목은 저장소 전체에서 위반 0이다. 기준선 파일을 두지 않는다.
- 포맷은 `ruff format`이 강제하므로 평가하지 않는다. 기능 정확성과 인수 조건 충족은 테스트와
  `domain-reviewer`, 보안은 `security-auditor`가 맡는다.

### 실행 위치와 재개발

| 시점 | 실행 | 동작 |
| --- | --- | --- |
| 파일 편집 직후 | `.codex/hooks/post_edit_feedback.py` | 편집한 파일에 `ruff format`과 `ruff check`를 실행하고 남은 위반을 모델에게 알린다. 막지 않는다. |
| 세션 종료 전 | `.codex/hooks/stop_gate.py` | Ruff, complexipy 고정 명령, `validate_conventions.py`, `validate_architecture.py`, `ty`, 관련 테스트를 실행하고 실패하면 종료를 막아 다시 고치게 한다. 같은 실패는 최대 3회까지 막는다. |
| 커밋·푸시 전 | `.pre-commit-config.yaml` | 커밋 단계에서 같은 스타일 검사를, 푸시 단계에서 테스트를 실행한다. |
| PR | `.github/workflows/ci.yml` | 최종 강제 지점이다. 훅은 꺼질 수 있으므로 CI가 기준이다. |

훅의 동작과 한계는 `.codex/hooks/README.md`를 따른다.

## 프로그램 판정 항목

모든 항목의 위반은 0이다.

| ID | 차원 | 판정 질문 | 도구·규칙 | 근거 |
| --- | --- | --- | --- | --- |
| CS-P01 | 제어 흐름 | `src` 함수의 인지 복잡도가 15를 넘거나, 기준선 파일·설정으로 검사를 피하는가? | complexipy 고정 명령, 검사기 | [clean-code](../.codex/skills/implementation/references/clean-code.md) Avoid Deep Nesting, #102, #118 |
| CS-P03 | 제어 흐름 | return 뒤 else, 합칠 수 있는 if, 불필요한 bool 반환, `try`-`except`-`pass`가 있는가? | Ruff `RET505`, `SIM102`, `PLR5501`, `SIM103`, `SIM105` | clean-code Prefer Clear Control Flow, #112 |
| CS-P04 | 함수 구성 | 위치 인자가 2개를 넘거나 전체 인자가 5개를 넘는가? | Ruff `PLR0917`(`max-positional-args = 2`), `PLR0913` | clean-code Minimize Parameters, #102, #118 |
| CS-P05 | 함수 구성 | bool 값을 위치 인자로 받거나 넘기는가? | Ruff `FBT001`, `FBT003` (`tests` 제외) | 같은 절, #102 |
| CS-P06 | 함수 구성 | `src`에서 매직 넘버와 비교하는가? | Ruff `PLR2004` (`src`만) | clean-code Searchability, #102 |
| CS-P07 | 이름 | Python 이름 표기 규칙을 어기거나, `...Exception` 이름과 `ApiException`을 HTTP 계층 밖에서 쓰는가? | Ruff `N`, 검사기 | clean-code Naming, #102 |
| CS-P11 | 계층·경계 | 다른 모듈을 허용되지 않은 방향으로 import하는가? | import-linter(`poe lint-imports`) | [ADR 0011](adr/0011-split-bounded-contexts-logically-with-layered-packages.md) 「강제 방법」 |
| CS-P12 | 계층·경계 | `exception_handlers.py` 밖에서 `HTTPException`을 쓰는가? | 검사기 | [ADR 0001](adr/0001-public-api-error-boundary.md) |
| CS-P13 | 계층·경계 | 트랜잭션 헬퍼(`platform/database/transactions.py`) 밖에서 `begin`·`commit`·`rollback`을 직접 호출하는가? | 검사기 | [ADR 0007](adr/0007-store-batch-receipts-with-activity-transactions.md), [ADR 0012](adr/0012-notify-cross-context-changes-with-in-process-domain-events.md), 저장소 관례 |
| CS-P15 | 저장소 관용구 | `src/mosemo/platform/clock.py` 밖에서 현재 시각 API를 호출하거나 `clock`을 키워드 전용 필수 인자로 받지 않는가? | Ruff `TID251`, 검사기 | #86 |
| CS-P17 | 저장소 관용구 | FastAPI 의존성을 `Annotated` 없이 선언하는가? | Ruff `FAST002` | architecture FastAPI Request Boundaries |
| CS-P18 | 오류 처리 | 넓은 `except`를 허용 위치 밖에서 쓰거나 예외를 조용히 삼키는가? | 검사기(`src`), Ruff `BLE001`(`src` 밖), `S110` | [ADR 0001](adr/0001-public-api-error-boundary.md), #102 |
| CS-P19 | async 경계 | async 함수에서 블로킹 호출을 하는가? | Ruff `ASYNC` (`tests` 제외) | architecture FastAPI Request Boundaries |
| CS-P20 | 테스트 | pytest 관용구를 어기는가? | Ruff `PT` | [testing](../.codex/skills/implementation/references/testing.md) |
| CS-P21 | 주석 | 주석 처리된 코드가 있는가? | Ruff `ERA001` | clean-code Comments |
| CS-P22 | 저장소 관용구 | 인라인 억제 주석을 쓰거나 검사 설정을 바꿔 규칙을 끄는가? | 검사기 | #102 |
| CS-P23 | 테스트 | 테스트가 시각 공급원(clock 인자, `get_clock` 재정의)을 lambda로 공급하는가? | 검사기 | #86 |
| CS-P24 | 저장소 관용구 | 시간대 없는 datetime을 만드는가? | Ruff `DTZ` | [ADR 0009](adr/0009-define-public-json-timestamps-and-responses.md) |
| CS-P26 | 저장소 관용구 | `IntegrityError`로 중복을 처리하는가? | 검사기 | 저장소 관례, #91 |
| CS-P27 | 함수 구성 | 의존성을 필드로 담은 묶음 객체로 인자 수를 줄이는가? | 검사기 | #118 |
| CS-P31 | 저장소 관용구 | 생성자(`__init__`, `__new__`)에 `@override`를 붙여 인자 수 규칙을 피하는가? | 검사기 | #118 |

### 항목별 정답

- **CS-P01**: `src` 함수의 인지 복잡도는 15 이하다. 실행 명령은
  `complexipy src --max-complexity-allowed 15 --isolated --no-ignore`로 고정해 설정 파일의 `exclude`와 인라인
  `# complexipy: ignore` 우회를 막는다. 저장소 루트의 `complexipy-snapshot.json`, `complexipy.toml`,
  `pyproject.toml`의 `[tool.complexipy]`는 위반이다. 기준선을 다시 만들어 고복잡도 함수를 통과시키는 우회를
  막는다.
- **CS-P04**: 위치 인자는 2개까지이고 세 번째 인자부터 키워드 전용(`*`)이다. `self`·`cls`는 세지 않는다.
  `tests`(pytest가 fixture를 이름으로 주입)와 `src/mosemo/entrypoints/api/middleware.py`(ASGI 서버가 위치 인자로 호출)는
  제외한다. 라이브러리가 위치 인자로 호출하는 오버라이드는 `@typing.override`를 붙인다.
  - 전체 인자는 5개까지다(`PLR0913`, `*args`·`**kwargs` 제외). `tests`는 제외한다.
  - 인자가 많으면 책임 단위로 나눈다. 허용하는 방법은 하위 협력자 추출, 업무 사건의 데이터를 담는 값 객체,
    조립 위치 이동(provider)이다. 의존성을 담은 묶음 객체는 CS-P27이 막는다.
  - UseCase·Repository·Query·공개 협력자의 역할과 조립은
    [ADR 0013](adr/0013-persist-dataclass-aggregates-with-imperative-mapping.md)과
    [ADR 0011](adr/0011-split-bounded-contexts-logically-with-layered-packages.md) 「조립」을 따른다.
- **CS-P07**: 예외 이름의 접미사가 계층을 나타낸다. `...Exception`은 HTTP 응답이 되는 `ApiException` 계열
  전용이며 `src/mosemo/shared/errors.py`에서만 정의한다. `ApiException`과 그 하위 클래스는 `router.py`,
  `entrypoints/api/exception_handlers.py`, `shared/errors.py`에서만 import·생성한다. 나머지 예외는
  `...Error`다. Ruff `N818`은 `*Exception` 이름을 무시하도록 설정하고 이 규칙은 검사기가 판정한다.
- **CS-P12**: `HTTPException`은 `src/mosemo/entrypoints/api/exception_handlers.py`에서만 import하거나 참조한다. router도
  허용하지 않는다. ADR 0001·0014에 따라 공개할 오류는 BC 오류 코드 enum과 `ApiException`으로 명시하고, 내부 오류는
  도메인 오류(`...Error`)로 던진다. 판정은 import 경로를 해석하지 않고 이름으로 한다. `ImportFrom`의 가져오는
  이름이나 속성 참조의 `attr`가 `HTTPException`으로 끝나면 위반이며, 허용 파일이 만든 별칭
  (`StarletteHTTPException`)을 다시 가져오는 경로도 막는다. `tests`·`scripts`·`migrations`·`.codex`는 대상이
  아니다.
  - Ruff `TID251` 대신 검사기로 판정한다. `banned-api`는 금지 항목마다 허용 경로를 줄 수 없어
    `exception_handlers.py`를 허용하려면 `per-file-ignores`에 `TID251`을 넣어야 하고, 그러면 그 파일에서
    CS-P15의 현재 시각 API 금지까지 꺼진다.
- **CS-P13**: 트랜잭션은 UseCase가 `platform.database.transactions`의 `unit_of_work`·`read_snapshot`으로 연다
  ([ADR 0012](adr/0012-notify-cross-context-changes-with-in-process-domain-events.md)).
  - 대상은 `src`의 모든 `.py` 파일이다. 메서드 이름이 `begin`·`begin_nested`·`commit`·`rollback`인 속성 호출이면
    수신 객체(`db_session`, session factory, engine)와 관계없이 위반이다. `flush`는 대상이 아니다.
  - 예외는 트랜잭션 헬퍼 `src/mosemo/platform/database/transactions.py` 하나이며 경로로 허용한다. 다른 경로의
    `transactions.py`는 허용하지 않는다.
  - 파일 이름으로 허용하던 옛 규칙(`service.py`·`executor.py`·`processing.py`)은 대상 파일이 없어져 없앴다.
- **CS-P15**: 시스템 시각은 `mosemo.platform.clock.utc_now`만 읽는다. 금지 API는 `datetime.now`,
  `datetime.utcnow`, `datetime.today`, `date.today`, `time.time`, `time.time_ns`이고 예외 파일은
  `src/mosemo/platform/clock.py` 하나다. 시각을 읽는 객체는 생성자에서 키워드 전용 필수 인자 `clock`을 받고, 트랜잭션
  또는 처리 단계마다 한 번 읽어 하위에 `now`로 넘긴다. 시각을 받기만 하는 객체는 `now` 인자로 받는다.
- **CS-P18**: `src`의 넓은 `except`(`Exception`, `BaseException`, 이를 포함한 튜플, 맨 `except:`)는 검사기의
  허용 위치 목록 `BROAD_EXCEPT_ALLOWED`(파일, 함수, 개수)에서만 쓴다. 목록에 있지만 코드에서 사라진 항목도
  위반이다. 이유 주석은 모델이 스스로 붙여 통과할 수 있으므로 허용 근거로 쓰지 않는다. 새 경계는 이유와 함께
  목록에 추가하며 리뷰 대상이다. 현재 허용 위치는 프로세스 경계 2(`entrypoints.consumer.__main__.main`,
  `entrypoints.batch.__main__.main`), supervisor 전달 1(`MessageConsumer.handle`), 로그인 콜백
  1(`identity/presentation/auth/router.create_oauth_router.callback`), 처리 단위 격리 2(`ProposeLabel.execute`, `execute_job`)다.
  Ruff `BLE001`은 로그만 남기는 넓은 `except`를 통과시켜 더 약하므로 `src`에서는 끄고 그 밖의 경로에서 쓴다.
- **CS-P22**: 인라인 억제 주석(`noqa`, `ruff: noqa`, `type: ignore`, `ty: ignore`, `pyright: ignore`,
  `complexipy: ignore`, `isort:`, `fmt:`, `pragma: no cover`)은 모두 금지한다. 경로별 예외는
  `pyproject.toml`의 `per-file-ignores`에만 두고, Ruff 설정 전체(선택 규칙, 옵션, 경로별 예외)는 검사기의
  고정 목록과 정확히 같아야 한다. `ruff.toml`·`.ruff.toml`은 두지 않는다. 규칙을 빼거나 옵션으로 약하게
  만드는 우회를 막는다.
- **CS-P23**: 테스트의 시각 공급원은 `FakeClock`(`tests/support/clock.py`)으로 공급하고, 실제 시스템 시각이
  필요한 곳(PyJWT 검증, APScheduler 실행)에만 `utc_now`를 쓴다. `clock=lambda: ...`와 `get_clock` 재정의에
  lambda를 돌려주는 lambda를 넣는 방식은 검사기가 막는다. 도메인 메서드에 고정 `datetime`을 넘기는 것은
  대상이 아니다.
- **CS-P24**: 직접 만드는 datetime은 `tzinfo=UTC`처럼 시간대를 가진다.
- **CS-P26**: 중복 처리는 Repository `get_or_create`의 `ON CONFLICT DO NOTHING`으로만 한다
  ([ADR 0013](adr/0013-persist-dataclass-aggregates-with-imperative-mapping.md)). `except IntegrityError`는
  유니크 충돌 외의 FK·NOT NULL·CHECK 위반과 같은 세션의 다른 flush 실패까지 "이미 있음"으로 덮고, PostgreSQL
  트랜잭션을 실패 상태로 만든다.
- **CS-P27**: `src`의 `@dataclass`(인자 포함 모든 형태)·`NamedTuple` 필드의 타입 주석에서 찾은 이름이
  `AsyncSession`, `Session`, `async_sessionmaker`, `sessionmaker`와 같거나 `Repository`, `Service`, `Clock`,
  `Config`, `Client`, `Resolver`, `Mapper`, `Queries`, `Commands`, `Provisioner`, `Processor`, `Scanner`,
  `Suggester`로 끝나면 위반이다. 이름은 Name, Attribute의 마지막 이름, `None`과의 합집합·`list[X]` 같은 내부
  이름, 문자열 주석 안의 이름에서 찾는다. `FocusSession` 같은 엔티티 이름과 `TypedDict`는 대상이 아니다.
  목록 밖 이름의 의존성은 리뷰가 맡는다.
- **CS-P31**: `__init__`·`__new__`에 import를 해석해 `typing.override`·`typing_extensions.override`로
  연결되는 데코레이터(별칭 포함)를 붙이지 않는다. 대상은 `src`·`tests`·`scripts`·`migrations`·`.codex`다.
  Ruff `PLR0913`·`PLR0917`은 `@override` 메서드를 건너뛰는데, 생성자는 항상 `object`의 메서드를 덮어쓰고
  ty도 생성자 시그니처 호환을 검사하지 않아 우회가 된다. 일반 메서드의 거짓 `@override`는 ty가 잡는다.

### 넣지 않은 규칙

| 규칙 | 이유 |
| --- | --- |
| Ruff `PLR1702` (중첩 깊이, 옛 CS-P02) | preview 규칙이라 Ruff 버전에 따라 동작이 바뀔 수 있고, 인지 복잡도가 중첩에 가산점을 주므로 CS-P01이 대신한다. |
| Ruff `C901` (순환 복잡도) | 분기 수만 세므로 중첩 if와 early return을 구분하지 못한다. CS-P01이 대신한다. |
| Ruff `ARG` (사용하지 않는 인자) | FastAPI 인증 의존성처럼 부작용을 위해 받는 인자가 많아 오탐이 대부분이다. |
| Ruff `FAST001` (불필요한 `response_model`) | `response_model` 명시가 OpenAPI 계약을 위한 의도적 스타일일 수 있다. |
| Ruff `TRY003` (예외 메시지 인자) | 정답이 정해져 있지 않다(기준 ②). |
| Ruff `ANN` (타입 주석) | `ty`가 이미 타입을 검사하고, 누락은 일부 특수 메서드뿐이다. |
| 업무 판단 위치의 일반 규칙(`XxxPolicy` 사용 기준) | 판단의 주어·출처를 가르는 기준마다 반례가 나와 정답 하나로 고정되지 않는다(기준 ②). 판단 위치는 CS-S02와 CS-J06·CS-J10·CS-J11로 채점하고, 기준은 object-design Tell, Don't Ask와 code-smells Feature Envy를 따른다(#118). |
| 허용 목록 밖의 로그 필드(옛 CS-P16) | `JsonFormatter`가 고정 필드와 `OPTIONAL_FIELDS`만 출력하므로 목록 밖 `extra=` 키는 런타임에 로그로 나가지 않는다. `extra=`에 함수 반환값을 넘기는 곳이 있어 정적 판정은 정상 코드를 잡거나 함수로 감싸 우회되며, 민감 값은 `SensitiveDataFilter`와 [로깅 규칙](logging.md)의 금지 목록이 다룬다. |

## 관련 문서

- [구현 Skill](../.codex/skills/implementation/SKILL.md)과 참조 문서
- [검증 Skill](../.codex/skills/verification/SKILL.md)
- [도메인 용어집](ubiquitous-language.md)
- [로깅 규칙](logging.md)
- [ADR 0001](adr/0001-public-api-error-boundary.md),
  [ADR 0007](adr/0007-store-batch-receipts-with-activity-transactions.md),
  [ADR 0009](adr/0009-define-public-json-timestamps-and-responses.md),
  [ADR 0011](adr/0011-split-bounded-contexts-logically-with-layered-packages.md),
  [ADR 0013](adr/0013-persist-dataclass-aggregates-with-imperative-mapping.md)

## 앞으로 할 일

이 절의 항목은 아직 강제하지 않는다. 항목을 구현하면 위 절로 옮긴다.

### 미구현 프로그램 항목

| ID | 차원 | 판정 질문 | 도구·규칙(예정) | 근거 | 현황(`e298365`) |
| --- | --- | --- | --- | --- | --- |
| CS-P08 | 이름 | 용어집의 `_Avoid_` 표현을 식별자에 쓰는가? | 검사기 | [용어집](ubiquitous-language.md) | 미측정 |
| CS-P09 | 이름 | 허용 약어 목록 밖의 약어를 쓰는가? | 검사기 | clean-code Don't Abbreviate | 미측정, 허용 목록 미결정 |
| CS-P14 | 계층·경계 | 역할 목록에 없는 새 파일 이름을 쓰는가? | 미정 | [ARCHITECTURE](../ARCHITECTURE.md) | 처음부터 다시 논의(아래) |

- **CS-P08**: 영어 `_Avoid_` 표현만 대상이다. `Device Registration`은 `device_registration`,
  `DeviceRegistration`처럼 snake_case와 PascalCase 토큰열로 바꿔 대조한다. 한국어 표현은 식별자에 쓰이지
  않으므로 대상이 아니다.
- **CS-P14**: 처음부터 다시 논의한다. 무엇을 막을지, 프로그램 항목으로 둘지, LLM 판정(CS-J01·CS-J06)에 맡길지,
  항목을 둘지 자체도 정하지 않았다. 판정 질문은 처음 후보 문장을 그대로 둔다. 다시 논의할 때 참고할 사실은
  다음과 같다. 결정이나 추천이 아니다.
  - 같은 분리 작업에서 모델마다 `validation.py`, `utils.py`, `domain.py`, 루트 `utils.py`, 새 하위 패키지처럼
    다른 파일·패키지를 만들 수 있다.
  - `74b74b3`에서 `src`의 표준 역할(`router`, `schemas`, `service`, `repository`, `*_repository`, `models`,
    `__init__`) 밖 파일은 루트 공용 모듈 14개를 포함해 38개였다. 이후 `mappers.py` 등이 추가되어 다시 세야 한다.
  - 파일 이름 허용 목록을 직접 지원하는 도구는 찾지 못했다. Ruff·import-linter·tach·deply는 파일 이름을
    다루지 않는다. ls-lint는 디렉터리별 이름 regex를 지원하지만 설정 파일로 우회할 수 있고, 목록에 있지만
    사라진 파일을 잡지 못한다.
  - 표준 밖 파일을 모두 경로로 등록하게 하는 방식은 정당한 새 파일마다 목록 변경과 승인이 필요하다. 이
    방식은 기준 ②·④에 맞는지 검토가 필요하다.

### LLM 판정 항목

후보를 프로그램이 찾는 항목은 그 위치에 대해서만 묻는다. 전체 코드에 "좋은가"를 묻지 않는다. 모든 질문은
"예"가 위반이다.

| ID | 차원 | 판정 질문 | 후보 탐지 | 근거 |
| --- | --- | --- | --- | --- |
| CS-J01 | 선례 일치 | 같은 역할의 기존 코드와 다른 방식으로 같은 종류의 문제를 풀었는가? | 프로그램이 다른 모듈의 같은 역할 파일을 비교 대상으로 제공 | 저장소 관례 |
| CS-J02 | 단순성 | 구현이 하나뿐인 `Protocol`에 테스트 대역이나 경계 역전 같은 실제 이유가 없는가? | 새 `Protocol`과 구현 클래스 수 | [complexity](../.codex/skills/implementation/references/complexity.md) KISS |
| CS-J03 | 단순성 | 호출하는 곳이 넘기지 않는 매개변수나 아무도 읽지 않는 필드가 현재 인수 조건에 근거 없이 있는가? | 미사용 매개변수·필드 | complexity YAGNI |
| CS-J04 | 함수 구성 | 한 줄짜리 래퍼가 도메인 의미 없이 이름만 바꾸는가? | 본문이 다른 함수 호출 하나뿐인 함수 | clean-code Split Functions Reasonably |
| CS-J05 | 중복 | 같은 업무 규칙이 세 곳 이상에 복제되어 있는가? | 유사 코드 탐지 | complexity Rule of Three |
| CS-J06 | 배치 | 코드가 파일 역할과 맞지 않는 곳에 있는가? (예: service에서 SQL 조립, repository에서 업무 검증) | 없음 | architecture |
| CS-J07 | 함수 구성 | 이름에 드러나지 않는 부작용(저장, 외부 호출, 상태 변경)이 있는가? | 없음 | clean-code Avoid Unexpected Side Effects |
| CS-J08 | 이름 | 기존 코드가 같은 개념에 쓰는 이름과 다른 이름을 썼는가? | 없음 | clean-code Consistency & Uniqueness |
| CS-J09 | 이름 | 이름이 용어집의 정의와 모순되는가? | 없음 | 용어집 |
| CS-J10 | 책임 | 객체의 불변식을 그 객체 밖에서 검사하거나 바꾸는가? | 없음 | [object-design](../.codex/skills/implementation/references/object-design.md) Encapsulation |
| CS-J11 | 책임 | service가 조율(로딩, 트랜잭션, 순서) 외에 도메인 판단을 직접 하는가? | 없음 | [implementation 구현 원칙](../.codex/skills/implementation/SKILL.md) |
| CS-J12 | 계층·경계 | ORM 모델의 필드로 도메인 판단을 하는가? | 없음 | implementation 절차 2 |
| CS-J13 | 테스트 | 도메인 테스트의 mock을 fake나 실제 객체로 바꿀 수 있는가? | 도메인 테스트의 `unittest.mock` 사용 | testing Test Doubles |
| CS-J14 | 테스트 | 테스트 이름이 행동 대신 구현 세부를 말하는가? | 없음 | testing Test Naming |
| CS-J15 | 주석 | 코드를 되풀이하는 주석이 있는가? | 없음 | clean-code When to Write Comments |

- **CS-J06**: repository는 조회한 엔티티·값 객체의 필드를 Python 식으로 비교해 업무 결정을 내리지 않고, 그
  결정은 엔티티·값 객체의 메서드를 호출해 얻는다. 조회 조건(SQL), 키 조회, 사용한 ID 집합 같은 순회 관리는
  대상이 아니다.

### LLM 점수 항목

정도가 본질이어서 예/아니오로 나누면 정보가 사라지는 항목이다. 각 0·1·2점이며, 단계마다 기준 예시를
고정한다. 변경된 함수(테스트는 테스트 함수) 단위로 채점한다.

#### CS-S01 한 가지 일과 추상화 수준

| 점수 | 기준 |
| --- | --- |
| 0 | 검증·조회·저장·알림처럼 서로 다른 이유로 바뀌는 일이 한 함수에 섞여 있다. |
| 1 | 주 흐름은 하나지만 다른 수준의 세부(SQL 조립, 문자열 파싱, 시간 계산)가 끼어 있다. |
| 2 | 모든 문장이 같은 수준에서 읽히고 세부는 이름 있는 연산에 맡긴다. |

근거: clean-code Do One Thing, Keep Functions Small.

#### CS-S02 판단의 위치

| 점수 | 기준 |
| --- | --- |
| 0 | 판단에 필요한 데이터를 다른 객체에서 꺼내 와 바깥에서 결정하고 상태를 바꾼다. |
| 1 | 일부 판단만 데이터를 가진 객체에 있고 나머지는 호출하는 쪽에 남아 있다. |
| 2 | 판단과 상태 변경을 데이터를 가진 객체나 협력하는 도메인 연산이 함께 소유한다. |

근거: object-design Tell, Don't Ask, code-smells Feature Envy.

#### CS-S03 테스트가 명세로 읽히는가

| 점수 | 기준 |
| --- | --- |
| 0 | 준비·실행·검증이 섞여 무엇을 검증하는지 알기 어렵다. |
| 1 | 구조는 보이지만 준비 과정이 길어 핵심 조건이 묻힌다. |
| 2 | 조건·행동·결과가 한눈에 보이고 반복되는 준비는 builder나 fixture에 맡긴다. |

근거: testing Arrange-Act-Assert, Test Builders.

### LLM 채점과 재개발 규칙

| 형태 | 실패 조건 | 처리 |
| --- | --- | --- |
| LLM 예/아니오(`CS-J`) | "위반" 판정 1건마다 | 수정하거나 반영하지 않는 근거를 기록 |
| LLM 점수(`CS-S`) | 변경된 단위 중 0점이 있거나 평균이 기준 미만 | 재개발 |

- 평가 대상은 이번 변경에서 추가·수정된 함수와 파일이다.
- 프로그램 항목을 모두 통과한 뒤에만 LLM 항목을 채점한다.
- LLM 판정은 근거 라인을 인용해야 유효하다. 근거 라인이 없는 지적은 무효로 처리한다.
- 같은 원인의 재개발은 최대 3회까지 반복한다. 이후에는 원인과 추천 대응을 보고하고 멈춘다.

LLM 항목을 재개발 게이트로 쓰기 전에 채점기의 일관성을 확인한다.

1. 항목마다 저장소 코드에서 통과 예와 위반 예를 2~3개씩 골라 정답을 붙인 기준 예시 세트를 만든다. 점수
   항목은 단계마다 예시를 둔다.
2. 고정한 채점 모델로 같은 코드를 3회 채점해 판정 간 일치도와 기준 예시와의 일치도를 잰다.
3. 일치도가 기준 미만인 항목은 게이트에서 빼고 참고 지적으로 낮춘다.
4. 루브릭이나 채점 모델을 바꾸면 기준 예시 세트로 다시 측정한다.

### 미결정

| 항목 | 추천 | 영향 |
| --- | --- | --- |
| 파일 역할 규칙 | 추천 없음. 막을 대상과 판정 방식(프로그램·LLM·두지 않음)부터 처음부터 다시 논의한다. | CS-P14, CS-J01, CS-J06 |
| 허용 약어 목록 | `id`, `url`, `uri`, `db`, `api`, `http`, `json`, `utc`, `uuid`, `oauth`, `pkce`, `ai`로 시작한다. | CS-P09 |
| 점수 평균 기준 | 현재 코드를 채점해 기준선을 본 뒤 정한다. | CS-S 판정 |
| 채점 모델 | Sonnet급 모델 하나로 고정한다. | 채점기 검증 |
