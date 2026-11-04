---
name: verification
description: 요구사항·모델 승인·문서 형식·품질 게이트·테스트 결과를 확인하고 실패를 제한적으로 수정할 때 사용한다.
---
# 검증

## 시작과 읽을 자료
사용자가 지정한 변경·동작·검증 범위를 대상으로 독립적으로 실행한다. 다른 Skill 실행이나 PRD·Plan Issue 생성을 선행 조건으로 삼지 않는다. `AGENTS.md`, 현재 코드·테스트와 사용자 요청의 기대 결과를 확인한다. 관련 PRD·ADR·Plan Issue·모델 승인이 있으면 함께 대조한다. 기대 결과가 불명확하면 가능한 기술 검사는 진행하되 해당 의미적 요구는 미검증으로 남기고 필요한 결정만 확인한다.

## 절차
1. 검증 범위를 명확히 하고 `uv run --locked python .codex/skills/verification/scripts/validate_docs.py`로 신규 문서의 기본 규격을 검사한다.
2. 변경 유형에 적합한 단위·컴포넌트·통합·마이그레이션·공개 API 계약 테스트를 수행한다.
3. `bash .codex/skills/verification/scripts/validate.sh`의 프로젝트 검사(코드 스타일·타입·빌드·테스트·diff)를 가능한 환경에서 실행한다.
4. PRD가 있으면 인수 조건별로, 없으면 요청의 기대 결과별로 **통과/실패/미검증** 및 증거를 연결한다. 테스트 통과가 의미적 요구사항의 정당성을 자동 보장하지는 않는다.
5. 변경에 관련된 객체의 책임과 협력, ORM과 도메인 모델 분리, 데이터 정합성, 선언된 확장 지점의 결합을 검토한다. 기존 모델 승인이 있으면 그 범위와도 대조한다.
6. 오류는 원인과 영향을 먼저 설명하고 관련 범위 안에서 수정한다. **동일 실패 원인 최대 3회** 재시도하며 실패를 숨기거나 검증 조건을 완화하지 않는다.
7. 남은 오류, 실행하지 못한 검사와 그 이유, 보완할 문서를 최종 보고한다.

## 종료 조건
필수 검사 실패나 승인된 모델의 실질적 이탈이 남으면 완료라고 보고하지 않는다. 사용자가 결정해야 할 대안에는 **추천값과 이유**를 함께 제시한다.

## 승인·추적·하네스 검증
- 관련 Plan Issue가 있을 때 아래 승인·AC 추적·Issue 본문 검사를 적용한다. 없으면 요청의 기대 결과와 검사 증거를 최종 응답에 보고하며, Issue 부재 자체를 검증 실패로 보지 않는다.
- Plan Issue의 승인 기록과 현재 대상·범위를 대조한다. 미승인/재승인 필요인 항목은 해당 범위 완료를 막는다. 상태 문자열만으로 실제 승인을 증명했다고 보고하지 않는다.
- 관련 PRD가 있으면 현재 범위 AC 목록과 Issue 추적 표를 대조하여 누락·중복·불필요한 범위 확대를 확인한다. 각 AC의 작업·증거·상태를 Issue 본문에 갱신한다. 실행 환경 문제는 이유와 함께 미검증으로 남긴다.
- 내보낸 Issue 본문에 `validate_docs.py --plan`을 실행한다. 정적 문서/구조 검사 통과와 의미적 설계 검토를 구분한다.

## 실행 도구

도구는 이 Skill의 `scripts/`에 둔다. 저장소 루트를 기준으로 다음 명령을 사용한다.

- `uv run --locked python .codex/skills/verification/scripts/validate_docs.py`: 7개 Skill과 참조 문서의 형식과 Requirement·PRD·ADR의 파일명, 필수 섹션, 자리표시자, 로컬 링크, 인수 조건 ID를 검사한다. 필수 섹션은 생성 Skill의 내장 템플릿에서 읽고 모든 문서를 검사한다.
- `uv run --locked python .codex/skills/verification/scripts/validate_docs.py --plan /tmp/plan.md`: 임시 내보낸 Plan Issue 본문의 승인 기록·작업 연결·검증 증거 형식과 연결한 로컬 문서의 AC 누락을 검사한다. 로컬 문서 링크는 저장소 루트 기준이다. 임시 본문을 Plan 복제본으로 저장소에 남기지 않는다.
- `uv run --locked python .codex/skills/verification/scripts/validate_architecture.py`: ADR 0001·0014에 따라 `shared.errors`·`shared.presentation.responses`·`api.exception_handlers`에서 OpenAPI 모듈(`shared.openapi`·`shared.error_docs`·`api.openapi`) 참조를 절대·상대·함수 내부 import까지 AST로 금지한다. BC 패키지(`src/mosemo/{bc}/public.py`가 있는 패키지, 있는 것만)에는 ADR 0011~0013 「강제 방법」의 AST 규칙을 적용한다. BC 루트의 허용 목록(`public.py`·`bootstrap.py`·`__init__.py`·계층 폴더) 밖 파일과 docstring 외 문장이 있는 루트 `__init__.py`, `application/ports.py` 밖의 `Protocol`, `application` 밖이거나 공개 메서드가 `async def execute` 하나가 아닌 `UseCase` 하위 클래스, `UseCase`를 상속하지 않고 `execute`를 가진 application 클래스, `UseCase` 하위 클래스 메서드 밖의 `unit_of_work`·`read_snapshot`, 라우트 함수에서 UseCase `execute` 외의 await 호출, `public.py`가 내보내는 application 클래스(공개 협력자)의 `UseCase` 상속·`execute`·트랜잭션 헬퍼 사용, `db_session`이 아닌 `AsyncSession` 매개변수, 생성자에서 세션을 받는 Repository·Query, infrastructure Query 파일(`query`·`queries`·`*_query`·`*_queries`)의 `insert`·`update`·`delete`·`add`·`add_all`, `domain/events.py` 밖이거나 `frozen`이 아니거나 이름이 과거분사(`-ed` 또는 불규칙 과거분사 허용 목록)가 아니거나 `Event` 접미사가 붙은 `DomainEvent` 하위 클래스와 `domain/events.py`의 비이벤트 클래스, `self.events.append` 외 문장이 있는 `mark_*` 메서드, infrastructure에서 domain 클래스 속성으로 만든 SQL 식을 금지한다. 매핑 dataclass 선언, PK·FK 이름, 매핑 클래스의 root 소속, Repository 타입과 root당 Repository 하나, `public.py`의 엔티티 노출, 라우터의 현재 계정 provider와 override 누락은 `tests/unit/harness/test_context_introspection.py`의 내성 테스트가 검사한다.
- `uv run --locked python .codex/skills/verification/scripts/validate_conventions.py`: 현재 시각 처리, 검사 억제, 트랜잭션·예외 경계, 복잡도 기준선 파일과 의존성 묶음 규칙을 검사한다. 테스트의 `clock=` lambda와 `get_clock` 재정의의 lambda 반환 lambda, `src`·`tests`·`scripts`·`migrations`·`.codex` 주석의 검사 억제 지시문(`noqa`, `ruff: noqa`, `type: ignore`, `ty: ignore`, `pyright: ignore`, `complexipy: ignore`, `isort:`, `fmt:`, `pragma: no cover`), 검사기의 허용 설정과 키·값이 다른 `pyproject.toml` Ruff 설정과 다른 Ruff 설정 파일, `src`의 `__init__`에서 키워드 전용 필수가 아닌 `clock` 매개변수, 허용 목록 밖 `src`의 넓은 `except`, 저장소 루트의 `complexipy-snapshot.json` 기준선 파일과 complexipy 설정, `shared/errors.py`의 `ApiException` 계층 밖 `...Exception` 클래스, `router.py`·`api/exception_handlers.py`·`shared/errors.py` 밖의 `ApiException` import·생성, `src`의 `exception_handlers.py` 밖에서 이름이 `HTTPException`으로 끝나는 import·속성 참조(CS-P12), `src`의 `@dataclass`·`NamedTuple` 필드에 의존성(`AsyncSession`·`Session`·`async_sessionmaker`·`sessionmaker`이거나 `Repository`·`Service`·`Clock`·`Config`·`Client`·`Resolver`·`Mapper`·`Queries`·`Commands`·`Provisioner`·`Processor`·`Scanner`·`Suggester`로 끝나는 타입)을 담은 묶음 객체(CS-P27), `src`의 `IntegrityError` 처리(CS-P26), `src`·`tests`·`scripts`·`migrations`·`.codex`의 `__init__`·`__new__`에 붙은 `typing.override`·`typing_extensions.override`(별칭 포함, CS-P31), `src`의 `infra/transactions.py` 밖에서 `begin`·`begin_nested`·`commit`·`rollback` 메서드 호출(CS-P13)을 금지한다. 파일 이름으로 역할을 정하던 옛 규칙(CS-P10·CS-P25·CS-P28~CS-P30·CS-P32)은 옛 패키지 제거와 함께 폐기했다. 위반마다 파일·라인·규칙 ID와 수정 안내를 출력한다.
- `uv run --locked complexipy src --max-complexity-allowed 15 --isolated --no-ignore`: `src` 함수의 인지 복잡도를 검사한다. 기준선 없이 15 초과는 실패하며, 기준선 파일 `complexipy-snapshot.json`을 만들지 않는다. `--isolated`·`--no-ignore`로 설정 파일의 제외와 인라인 `# complexipy: ignore`를 무효로 하며, 명령 인자를 바꾸지 않는다.
- `bash .codex/skills/verification/scripts/validate.sh`: 문서·구조·코드 규칙 검사와 Ruff·complexipy·포맷·타입·빌드·전체 테스트·diff 검사를 실행한다. 스크립트 위치에서 저장소 루트를 찾아 실행한다.

검사기 회귀 테스트는 `tests/unit/harness/`에 둔다. 문서·구조 검사와 회귀 테스트는 Git 훅과 CI에서도 실행한다. 로컬 훅 설치는 `uv run --locked pre-commit install --hook-type pre-commit --hook-type pre-push`로 수행한다. 훅은 우회 가능하며 원격 필수 체크 설정은 별도 확인한다.

정적 검사 통과는 승인 진위, 외부 링크 실존, 의미적 요구사항의 완전성이나 동적 import의 안전성을 보장하지 않는다. 검사 규칙을 변경해 위반을 숨기지 않는다.
