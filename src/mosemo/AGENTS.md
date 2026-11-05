# src/mosemo 작업 규칙

이 문서는 요지만 담는다. 기준은 ADR이고, 강제는 `poe lint-imports`·Ruff·
`.codex/skills/verification/scripts/validate_architecture.py`·`.codex/skills/verification/scripts/validate_conventions.py`·
`tests/unit/harness/`가 한다.
역할 규칙 표는 implementation 스킬 「Mosemo 코드 역할 규칙」의 요약이므로, 원본을 바꾸면 이 표도 함께 고친다.

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
| router(`presentation`) | HTTP 입출력 변환과 UseCase `execute` 호출만 한다. 도메인 오류를 BC 오류 코드의 `ApiException`으로 바꾼다([ADR 0014](../../docs/adr/0014-align-public-identifiers-and-error-codes-with-contexts.md)). |
| Mapper(`presentation`) | 스키마 ↔ 엔티티·application 값의 상태 없는 변환만 한다. 조회·저장·시각 읽기를 하지 않는다. |
| UseCase(`application`) | `shared`의 `UseCase`를 상속하고 유스케이스 하나에 클래스 하나, 공개 메서드는 `async def execute`만 둔다. 트랜잭션은 `unit_of_work`(쓰기)·`read_snapshot`(읽기)으로만 열고 `begin`·`commit`·`rollback`을 직접 호출하지 않는다(CS-P13). 업무 판단은 aggregate에 맡긴다. |
| 공개 협력자(`application`, `public.py`로 공개) | 첫 인자로 `db_session`을 받는다. 트랜잭션을 열지 않는다. 메서드 이름은 업무 이름으로 짓는다. |
| Repository(`infrastructure`) | aggregate root마다 하나씩, 통째로 불러오고 저장한다. 상태·판단·트랜잭션이 없다. 세션은 생성자가 아니라 메서드 첫 인자 `db_session`으로 받는다. 동시 생성은 `get_or_create`의 `ON CONFLICT DO NOTHING`으로 하고 `IntegrityError`를 잡지 않는다(CS-P26). |
| Query(`infrastructure`) | 읽기 모델만 반환한다. 데이터를 바꾸지 않고 엔티티를 반환하지 않는다. 세션은 메서드 첫 인자 `db_session`으로 받는다. |
| 엔티티(`domain`) | dataclass aggregate·값 객체다. SQLAlchemy·FastAPI·Pydantic을 import하지 않는다. `Table`과 imperative mapping은 infrastructure에 둔다([ADR 0013](../../docs/adr/0013-persist-dataclass-aggregates-with-imperative-mapping.md)). |

- 의존성(repository·session·clock 등)을 묶은 객체로 인자 수를 줄이지 않는다.
- 시각을 읽는 객체는 키워드 전용 필수 인자 `clock`을 받는다.
- 전체 규칙과 규칙 ID: [implementation 스킬 「Mosemo 코드 역할 규칙」](../../.codex/skills/implementation/references/architecture.md)
