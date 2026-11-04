---
status: accepted
date: 2026-11-04
---
# 도메인 모델은 dataclass aggregate로 두고 imperative mapping으로 영속화하며, 쓰기는 UseCase·Repository, 읽기는 Query로 나눈다

## 맥락

현재 엔티티 10개는 모두 `DeclarativeBase`를 상속하고 `mapped_column`(82개), 제약·인덱스·FK(57개),
`__table_args__`(8개)를 클래스 안에 둔다. `relationship`은 없고 엔티티마다 repository가 있다.
repository는 생성자에서 `AsyncSession`을 받으므로 요청마다 다시 만들어야 하고, service도 같은
이유로 요청 단위 객체다. 같은 repository가 쓰기용 조회(`FOR UPDATE`)와 화면용 목록 조회를 함께
맡는다.

[ADR 0011](0011-split-bounded-contexts-logically-with-layered-packages.md)은 BC마다 `domain`과
`infrastructure` 계층을 나누고 domain이 SQLAlchemy를 import하지 않도록 정했다.
[ADR 0012](0012-notify-cross-context-changes-with-in-process-domain-events.md)는 aggregate root가
이벤트를 기록하고 `unit_of_work`가 수거하도록 정했다.

Account 하나를 imperative mapping으로 옮기는 스파이크(기준 `dea5e93`, 코드는 폐기)에서 다음을
확인했다. 통과 기준(ruff, ty, complexipy, 전체 테스트 840건과 커버리지, Alembic 비교 diff가 기존
drift 2건과 같음)은 모두 통과했다.

- 순수 dataclass의 클래스 속성으로 만든 SQL 식(`Account.provider == p`)은 런타임에서는 매핑 후
  정상 동작하지만 ty는 `bool`(단일 멤버 enum이면 `Literal[True]`)로 판단해 오류를 낸다. Account
  관련 67곳에서 오류가 났고 그중 2곳은 ty가 놓쳤다. 67곳 중 61곳은 테스트 정리 코드의 같은 줄
  반복이었다.
- SQLAlchemy 문서는 imperative mapping이 PEP 484 지원을 제공하지 않는다고 밝힌다. 클래스 속성에
  타입을 씌우는 자체 헬퍼는 공식 근거가 없다.
- SQLAlchemy는 `where(True)`를 `WHERE true`로 만든다. 인스턴스 속성으로 쓴 식은 오류 없이 전체
  행을 조회한다.
- `server_default` 컬럼에는 dataclass 기본값을 두지 않아야 하고, `uuid7` 같은 Python 기본값은
  Core `insert()` 경로를 위해 컬럼 기본값으로도 남겨야 한다.
- dataclass가 만드는 `__eq__`·`__hash__`·`__repr__`은 엔티티와 맞지 않는다.

선택 기준은 다음과 같다.

- domain이 SQLAlchemy를 모르는가
- `ty check`를 끄지 않고 쿼리 식의 타입 오류를 잡는가
- 공식 지원 범위의 API만 쓰는가
- aggregate 경계 밖에서 하위 엔티티를 바꾸지 못하게 하는가
- Repository와 UseCase를 앱 시작 때 한 번 만들어 API·Consumer·batch가 공유할 수 있는가
- 쓰기와 읽기의 트랜잭션 규칙을 도구로 구분할 수 있는가

## 결정

### 도메인 모델

- 엔티티는 `@dataclass(eq=False, repr=False, kw_only=True)`로 선언하고 `slots`는 쓰지 않는다.
  생성 시 불변식은 `__post_init__`에서 확인한다.
- 값 객체, 공개 읽기 모델, 이벤트는 `@dataclass(frozen=True)`로 선언한다.
- 기반 클래스는 `shared`의 `AggregateRoot`(이벤트 목록)와 `DomainEvent`만 둔다. `Entity`와
  `ValueObject` 기반 클래스는 두지 않는다.
- DB가 채우는 값(`server_default`)은 `field(init=False)`로 선언하고 기본값을 두지 않는다. Python
  기본값은 dataclass `default_factory`와 `Column(default=...)`에 함께 둔다.

### 식별자 이름

- 엔티티 자신의 식별자는 엔티티 속성과 DB 컬럼 모두 `id`로 한다. 기존 PK 컬럼은 이름을 바꾼다.
- 다른 엔티티를 가리키는 참조는 `{엔티티}_id`로 한다(외래키, 이벤트 필드, 매개변수).
- 예외는 역할이 붙은 참조(`first_event_id`), 클라이언트가 만든 식별자인
  `ActivityRecord.event_id`, 외부 값에서 나온 자연 키인 `NativeAuthCode.code_digest`(인가 코드의
  해시)다. 복합 PK 테이블에는 `id`를 두지 않는다.
- 공개 API의 식별자 이름은 [ADR 0014](0014-align-public-identifiers-and-error-codes-with-contexts.md)를
  따른다.

### 영속화

- `Table` 정의와 `registry.map_imperatively`는 각 BC의 `infrastructure`에 두고, 매핑 함수는
  `bootstrap.py`가 조립 루트에 제공한다.
- SQL 식은 infrastructure에서 `Table` 컬럼(`focus_sessions.c.account_id`)으로 작성한다. domain
  클래스의 속성으로 SQL 식을 만들지 않는다. `select(FocusSession)`, `insert(FocusSession)`,
  `.returning(FocusSession)`처럼 엔티티 단위로 쓰는 것은 그대로 둔다.
- 단일 컬럼을 select한 결과는 타입이 `Any`가 되므로 Repository·Query 메서드의 반환 타입 선언으로
  경계를 지킨다.
- 하위 엔티티는 root의 매핑에서 `relationship`으로 연결한다. 이 연결이 곧 "이 root의 하위"라는
  선언이다.
- 같은 요청의 재시도나 동시 요청이 있을 수 있는 생성(계정, 기기 등록, 집중 세션 시작, 활동
  레코드)은 Repository의 `get_or_create(db_session, entity) -> tuple[Entity, bool]`로 한다. 안에서는
  ORM-enabled `insert(Entity).values(...).on_conflict_do_nothing(...).returning(Entity)`로 넣는다.
  새로 넣었으면 돌아온 엔티티(`server_default` 값 포함, 세션에 추적됨)와 `True`를, 충돌이면 기존
  엔티티를 조회해 `False`와 함께 돌려준다. Core `insert(table)`은 `Row`를 돌려주고 identity map에
  들어가지 않아 다시 조회해야 하므로 쓰지 않는다. `created`가 `False`일 때 내용이 다르면 충돌로
  거절하는 판단(예: 집중 세션의 같은 ID·다른 내용)은 UseCase나 aggregate가 한다.
- 다른 BC의 테이블과는 조인하지 않고 직접 조회하지도 않는다([ADR 0011](0011-split-bounded-contexts-logically-with-layered-packages.md)).
  필요한 데이터는 그 BC의 공개 협력자로 받아 UseCase가 조립한다. 예: labeling의 확정 이력 예시
  조회는 자기 확정을 페이지로 읽고, 그 확정들의 구간을 observation 공개 협력자에서 한 번에 받아,
  구간이 없는 확정을 건너뛴다.

### 쓰기와 읽기의 역할

| 이름 | 계층 | 역할 |
| --- | --- | --- |
| UseCase | application | 유스케이스 하나. 쓰기 UseCase는 Repository로 aggregate를 불러와 aggregate 메서드로 바꾸고 `unit_of_work`를 연다. 읽기 UseCase는 Query로 읽기 모델을 받는다. |
| Repository | infrastructure | aggregate root 하나를 통째로 불러오고 저장한다. root마다 하나만 두고, 판단과 트랜잭션은 하지 않는다. |
| Query | infrastructure | 화면이나 다른 BC가 필요로 하는 읽기 모델을 SQL로 읽는다. 데이터를 바꾸지 않고 엔티티 객체를 돌려주지 않는다. |
| 공개 협력자 | application | 다른 BC의 UseCase가 자기 트랜잭션 안에서 부르는 이 BC의 입구. `public.py`로 내보낸다. 메서드 첫 인자로 `db_session`을 받고 트랜잭션을 열지 않으며, 메서드 이름은 업무 이름이다(`execute` 금지). 이 BC의 Repository와 Query만 쓴다. 예: `LabelCatalog.is_active`, `DeviceDirectory.find_owned`, `ObservationTimeline.lock`·`find_segment` |

- UseCase는 `shared`의 `UseCase` 기반 클래스를 상속하고 공개 메서드 `async def execute`만 둔다.
  `execute`의 시그니처는 UseCase마다 정한다. 유스케이스마다 클래스 하나를 두고, 파일은 개념별로
  묶는다(`application/focus_sessions.py`에 `StartFocusSession`, `CompleteFocusSession`,
  `ListCompletedFocusSessions`).
- 로직이 없는 조회도 UseCase를 거친다. presentation은 infrastructure를 import하지 않는다.
- Repository, Query, UseCase는 상태가 없다. DB 세션은 생성자가 아니라 메서드의 첫 인자
  `db_session: AsyncSession`으로 받는다.
- aggregate root만 Repository를 갖는다. 하위 엔티티 전용 Repository는 두지 않고, 하위 엔티티
  변경은 root 메서드로만 한다.
- 하위 엔티티 한 건이 필요할 때는 목적에 따라 나눈다.
  - 보여주기만 할 때: Query가 읽기 모델로 돌려준다.
  - 바꿀 때: root Repository로 root를 불러와 root 메서드로 바꾼다. Repository는 필요한 하위만
    채운 root를 돌려줄 수 있다. 부분 로딩이 자주 필요하면 aggregate 경계가 너무 크다는 신호로
    보고 경계를 다시 나눈다.
  - 쓰기 중 판단 근거로 읽을 때: 같은 `db_session`으로 Query를 부른다. 일관성이 필요하면 먼저
    잠금을 잡는다.

### 트랜잭션

- 트랜잭션은 UseCase만 연다. 쓰기는 `unit_of_work`, 읽기는 `read_snapshot`(읽기 전용, 이벤트
  없음)을 쓴다. 조회 하나만 하는 읽기 UseCase도 `read_snapshot`을 연다.
- 한 UseCase가 트랜잭션을 여러 번 열 수 있다. 외부 호출(OAuth 공급자, AI 제안기)은 트랜잭션
  밖에서 하고, 그 앞뒤로 짧은 트랜잭션을 연다. 트랜잭션을 여는 코드는 UseCase 클래스의
  메서드(`execute`와 그 클래스의 private 메서드) 안에 둔다.
- UseCase의 모든 DB 접근은 `unit_of_work`나 `read_snapshot` 블록 안에서 한다. 요청 하나 안에서
  같은 세션을 여러 UseCase가 함께 쓸 수 있으므로, 블록 밖의 조회가 남긴 autobegin 트랜잭션은
  다음 블록 진입을 오류로 만든다([ADR 0012](0012-notify-cross-context-changes-with-in-process-domain-events.md)).
  현재 계정 provider는 토큰만 검증하고 DB를 조회하지 않는다([ADR 0011](0011-split-bounded-contexts-logically-with-layered-packages.md)).
- Repository, Query, domain, BEFORE_COMMIT handler, presentation, 조립 루트는 트랜잭션을 열지
  않는다. AFTER_COMMIT handler의 트랜잭션은 그 단계를 구현할 때 정한다.

### 강제 방법

| 규칙 | 도구 |
| --- | --- |
| 매핑된 클래스의 dataclass 선언(`eq=False`, `repr=False`, `kw_only=True`, slots 없음) | 내성 단위 테스트 |
| PK 컬럼 `id`, FK 컬럼 `{참조 테이블 단수}_id`(예외 목록 포함) | 내성 단위 테스트 |
| 매핑된 클래스는 `AggregateRoot`이거나 어느 root에서 `relationship`으로 연결된 하위 | 내성 단위 테스트 |
| `AggregateRoot` 하위 클래스마다 Repository 하나, Repository의 인자·반환 타입은 root만 | 내성 단위 테스트(`get_type_hints`) |
| `public.py`가 매핑된 엔티티를 내보내지 않음 | 내성 단위 테스트 |
| 공개 협력자(`public.py`가 내보내는 application 클래스)는 `UseCase`를 상속하지 않고, `execute`가 없고, `unit_of_work`·`read_snapshot`을 쓰지 않음 | AST 검사기 |
| Core `insert(table)`을 aggregate 생성에 쓰지 않음(`get_or_create`는 ORM-enabled insert) | 리뷰 |
| infrastructure의 SQL 식에서 domain 클래스 속성 사용 금지 | AST 검사기(별칭·간접 참조는 놓칠 수 있음), ty 보조 |
| `AsyncSession` 매개변수 이름은 `db_session` | AST 검사기 |
| Repository·Query는 생성자에서 세션을 받지 않음 | AST 검사기 |
| Query 파일에 `insert`, `update`, `delete`, `add` 금지 | AST 검사기 |
| `UseCase` 하위 클래스는 application에만, 공개 메서드는 `async def execute` 하나 | AST 검사기 |
| application에서 `execute`를 가진 클래스는 `UseCase` 상속 | AST 검사기 |
| `unit_of_work`, `read_snapshot`은 `UseCase` 하위 클래스의 메서드 안에서만 | AST 검사기 |
| 라우터는 UseCase의 `execute`만 호출 | AST 검사기 |
| `DeclarativeBase`, `mapped_column` 재도입 금지 | Ruff `TID251` |

리뷰로 확인할 것: aggregate 경계가 불변식 단위로 묶였는가, 업무 판단이 UseCase로 새지 않고
aggregate에 있는가, 하위 엔티티를 root 밖에서 바꾸지 않는가, Query가 필요한 만큼만 읽는가.

## 고려한 대안

| 대안 | 장점 | 단점·비용 |
| --- | --- | --- |
| Declarative 모델을 그대로 `domain/`에 둠 | 공식 지원(`Mapped`)으로 클래스 속성 쿼리의 타입이 맞고 전환 비용이 없다. | domain이 테이블, 제약, FK 정의와 SQLAlchemy를 품는다. |
| imperative mapping + 클래스 속성 쿼리를 타입 헬퍼(`sql(Account.x)`)로 감쌈 | 도메인 이름으로 쿼리하고 단일 컬럼 결과 타입도 유지된다. | 공식 근거가 없는 자체 우회책이고 ty·SQLAlchemy 버전에 따라 깨질 수 있다. 감싸기 누락을 따로 막아야 한다. |
| imperative mapping + `filter_by(키=값)` | 클래스 속성을 쓰지 않는다(Cosmic Python 방식). | 동등 비교만 가능하고 정렬·범위·`in_`·`is_not`을 쓸 수 없다. 속성 이름 오타를 정적으로 못 잡는다. |
| imperative mapping + ty 규칙 끄기 | 수정이 가장 적다. | `where(account.x == p)`가 `WHERE true`가 되는 실수를 놓친다. |
| domain 필드를 `Mapped[X]`로 주석 | 클래스 속성 쿼리의 타입이 맞는다. | domain이 SQLAlchemy를 import하고, dataclass 생성자 인자 타입도 `Mapped[X]`가 되어 직접 `__init__`을 써야 한다. |
| Repository가 생성자에서 세션을 받음(현재) | 메서드 인자가 짧다. | 요청마다 객체를 다시 만들어야 하고 API·Consumer·batch가 조립을 공유하기 어렵다. |
| Repository에 UoW 객체를 넘김 | application이 SQLAlchemy 타입을 모른다. | 공개 `UnitOfWork` 타입과 조회용 UoW가 더 필요하지만, Repository는 결국 그 안의 세션을 꺼내 쓴다. |
| 개념별 `Commands`·`Queries` 클래스 | 클래스 수가 적다. | 쓰기와 읽기 의존이 한 클래스에 섞이고 "Query"가 조회용 저장소와 이름이 겹친다. |
| CQRS 요청 객체 + Handler + 버스 | 요청을 데이터로 다룰 수 있다. | 유스케이스마다 클래스가 둘이 되고 이벤트 디스패처와 비슷한 버스가 하나 더 생긴다. |
| UseCase 표시를 `Protocol`로 | 구현 강제처럼 보인다. | `execute` 시그니처가 UseCase마다 달라 공통으로 선언할 수 없고, `Protocol`은 상속 없이 맞추는 구조적 타입용이다. |
| `Entity`, `ValueObject` 기반 클래스 | 역할이 이름으로 드러난다. | 넣을 공통 동작이 없다. 식별은 identity map과 `eq=False`, 값 비교는 `frozen` dataclass가 이미 한다. |

## 결과와 트레이드오프

- domain 파일에는 SQLAlchemy가 없고, 테이블·제약·매핑은 infrastructure에 모인다.
- 쿼리 식을 `Table` 컬럼으로 바꿔야 한다. src에 약 92곳이 있으며 BC 이동 작업에서 함께 바꾼다.
  테스트 정리 코드의 반복(`delete(Account).where(...)`)은 헬퍼로 모은다.
- 매핑 등록을 빠뜨리면 실행 중 `UnmappedInstanceError`가 난다. 매핑은 BC의 `bootstrap.py`가
  제공하고 조립 루트와 Alembic 환경, 테스트 설정이 같은 함수를 부른다.
- PK 컬럼 이름 변경 마이그레이션이 필요하다. PostgreSQL은 컬럼 이름을 바꿔도 이를 가리키는
  외래키 제약을 유지한다. 서버를 운영하지 않는 기간이라 호환 기간 없이 적용한다.
- 단일 컬럼 select 결과는 메서드 안에서 타입 검사가 약하다.
- Repository·Query·UseCase를 앱 시작 때 한 번 만들어 공유하므로 요청마다 객체를 만들지 않는다.
- 하위 엔티티가 생기면 aggregate 경계 설계가 먼저 필요하다. 지금은 모든 엔티티가 단일 엔티티
  aggregate다.

## 대체한 ADR

없음

## 연관 PRD·ADR

- [ADR 0011](0011-split-bounded-contexts-logically-with-layered-packages.md)
- [ADR 0012](0012-notify-cross-context-changes-with-in-process-domain-events.md)
- [ADR 0014](0014-align-public-identifiers-and-error-codes-with-contexts.md)
- [ADR 0007](0007-store-batch-receipts-with-activity-transactions.md): 배치 영수증은 활동 기록과 같은
  트랜잭션에 저장하는 규칙을 유지한다.
- [ADR 0010](0010-run-batch-jobs-in-apscheduler-process.md): 배치 job 흐름도 UseCase가 트랜잭션을
  연다.
