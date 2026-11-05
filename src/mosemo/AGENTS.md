# src/mosemo 작업 규칙

기준은 ADR이고, 강제는 `poe lint-imports`·Ruff·
`.codex/skills/verification/scripts/validate_architecture.py`·`.codex/skills/verification/scripts/validate_conventions.py`·
`tests/unit/harness/`가 한다.

## 패키지 배치

- BC 사이 의존: `labeling` → `observation` → `labels` → `identity`. 왼쪽 BC만 오른쪽 BC를 import한다(건너뛴 import 허용)
  ([ADR 0011](../../docs/adr/0011-split-bounded-contexts-logically-with-layered-packages.md)).
  상류 BC가 하류의 변경을 알아야 하면 import하지 않고 도메인 이벤트로 알린다
  ([ADR 0012](../../docs/adr/0012-notify-cross-context-changes-with-in-process-domain-events.md)).
- BC 루트 파일은 `public.py`(다른 BC·조립 루트용)·`bootstrap.py`(조립 루트·Alembic 전용)·`__init__.py`(docstring만 허용)만
  두고, 그 밖의 코드는 계층 폴더 4개에 둔다.
  다른 BC 코드는 `public.py`로만 쓰고, 다른 BC 테이블은 조회·조인하지 않는다.
- BC 내부 계층: presentation → application → infrastructure → domain. presentation은 infrastructure를 직접
  import하지 않는다. 포트(`Protocol`)는 외부 시스템 경계에만 `application/ports.py`로 둔다.
- `shared`: 둘 이상의 BC가 쓰고 업무 의미가 없는 것만 둔다. 엔티티·repository·업무 규칙은 두지 않는다.
  BC domain은 `shared.domain`만, application·infrastructure는 `shared.domain`·`shared.application`만 import한다.
- `platform`: 업무를 모르는 실행 기반. BC 계층별로 import할 수 있는 모듈은
  [ADR 0015](../../docs/adr/0015-group-entrypoints-and-platform-packages.md)의 표를 따른다. `faststream`·`aiokafka`는 `platform`만,
  `apscheduler`는 `platform.scheduling`만 import한다.
- 설정: BC는 환경 변수·설정 클래스를 읽지 않는다. 필요한 값은 BC `bootstrap.py`가 정의한 순수 타입으로 조립 루트가
  생성자에 넘긴다(ADR 0015 「설정 소유 규칙」).
- `entrypoints/{api,consumer,batch}`: 조립 루트. BC의 `bootstrap.py`·`public.py`만 import한다.
  BC·`shared`·`platform`은 `entrypoints`를 import하지 않는다.

## 역할 규칙

| 역할(위치) | 규칙 |
| --- | --- |
| router(`presentation`) | HTTP 입출력만 바꾸고 UseCase의 `execute`만 호출(await)한다. 공개할 오류는 도메인 오류(`...Error`)를 BC 오류 코드의 `ApiException`으로 바꾼다(CS-P07, [ADR 0014](../../docs/adr/0014-align-public-identifiers-and-error-codes-with-contexts.md)). infrastructure를 직접 import하지 않는다. router는 presentation의 router 생성 함수가 `bootstrap.py`에서 조립한 UseCase를 받아 만든다. |
| Mapper(`presentation`) | API 요청 스키마와 엔티티·application 입출력 값 사이의 상태 없는 변환을 맡는다(`XxxMapper.to_entity`, 응답 방향 `to_response`). 조회·저장·현재 시각 읽기를 하지 않는다. |
| UseCase(`application`) | `shared`의 `UseCase`를 상속하고 유스케이스 하나에 클래스 하나, 공개 메서드는 `async def execute`만 둔다. 트랜잭션은 UseCase만 쓰기는 `unit_of_work`, 읽기는 `read_snapshot`으로 연다(ADR 0013). `begin`·`commit`·`rollback`을 직접 호출하지 않는다(CS-P13). 업무 판단은 aggregate 메서드에 맡기고 UseCase는 로딩·순서·트랜잭션·현재 시각 읽기를 조율한다. |
| 공개 협력자(`application`, `public.py`로 공개) | 다른 BC의 UseCase가 자기 트랜잭션 안에서 부르는 입구다. 첫 인자로 `db_session`을 받고 트랜잭션을 열지 않으며 `execute`가 아닌 업무 이름의 메서드를 둔다. |
| Repository(`infrastructure`) | aggregate root 하나를 통째로 불러오고 저장한다. root마다 하나만 두고 상태가 없으며 판단과 트랜잭션은 하지 않는다. 세션은 메서드 첫 인자 `db_session`으로 받는다. 재시도·동시 요청이 있는 생성은 `get_or_create`의 `ON CONFLICT DO NOTHING`으로 하고 `IntegrityError`를 잡지 않는다(CS-P26). 조회한 필드를 Python 식으로 비교해 업무 결정을 내리지 않는다. |
| Query(`infrastructure`) | 화면이나 다른 BC가 쓰는 읽기 모델을 읽는다. 데이터를 바꾸지 않고 엔티티를 돌려주지 않는다. 세션은 메서드 첫 인자 `db_session`으로 받는다. |
| 엔티티(`domain`) | dataclass aggregate와 값 객체다. `Table`과 imperative mapping은 infrastructure에 두고([ADR 0013](../../docs/adr/0013-persist-dataclass-aggregates-with-imperative-mapping.md)) `DeclarativeBase`·`mapped_column`을 쓰지 않는다(Ruff `TID251`). SQLAlchemy·FastAPI·Pydantic을 import하지 않는다. |
| 묶음 객체 금지 | 필드가 의존성(repository, session, clock, config, client 등)인 데이터 클래스로 인자 개수를 줄이지 않는다. 업무 사건의 데이터를 담는 값 객체는 허용한다(CS-P27). |
| 생성자 | `__init__`·`__new__`에 `@override`를 붙여 인자 수 규칙을 피하지 않는다(CS-P31). 시각을 읽는 객체는 키워드 전용 필수 인자 `clock`을 받는다(CS-P15). |
| 같은 타입 두 객체 비교 | 저장된 쪽을 주어로 한다(`stored.has_same_start(candidate)`). |

## 설계 원칙

- 객체의 책임과 협력을 중심으로 설계한다. 계산·변환·판단은 가능한 한 명시적인 입력과 반환값을 갖는 순수함수로 구현하고, I/O와 외부 부수 효과는 애플리케이션 경계에서 조율한다. 객체의 불변식과 상태 전이는 해당 객체가 소유하며, 순수함수로 분리하기 위해 책임을 객체 밖으로 옮기지 않는다.
- 여러 객체에 걸친 판단은 협력하는 도메인 연산에 둔다. 애플리케이션 서비스는 로딩·트랜잭션·잠금·외부 호출·순서를 조율한다.
- 도메인 규칙 없는 대입에 메서드를 만들거나 모든 원시값을 감싸지 않는다. 저장 형태만으로 도메인 책임을 정하지 않는다.
- 함수·클래스 크기, 필드·매개변수 수, 들여쓰기·else 사용은 가독성과 응집도의 검토 신호다. 고정 수치를 맞추기 위한 분리·추상화는 하지 않는다.
- 패턴은 현재 변경 문제가 요구할 때만 도입한다.
