# Mosemo

## 프로젝트 구조

- 디렉터리와 모듈 책임의 탐색 시작점: [ARCHITECTURE.md](ARCHITECTURE.md)
- 문서 탐색 시작점: [docs/index.md](docs/index.md)
- `src/mosemo/` 코드를 수정하기 전에 [src/mosemo/AGENTS.md](src/mosemo/AGENTS.md)를 읽는다.
- 테스트를 추가·이동하기 전에 [tests/AGENTS.md](tests/AGENTS.md)를 읽는다.

- 애플리케이션 코드: `src/mosemo/` (FastAPI, Pydantic, SQLAlchemy)
- 테스트: `tests/unit/{패키지}/`, `tests/integration/{패키지}/`, 공유 코드 `tests/support/` (pytest)
- 데이터베이스 마이그레이션: `migrations/` (Alembic)
- 공개 API 스냅샷: `openapi/openapi.json`
- OpenAPI 내보내기 스크립트: `scripts/export_openapi.py`

## 개발 환경

- Python 3.14 이상과 `uv`를 사용한다.
- 잠금 파일을 변경하지 않는 설치는 `uv sync --locked`로 실행한다.
- 로컬 개발 서버는 `uv run --env-file .env fastapi dev`로 실행한다.
- `.env`, `.env.test` 등 비밀값이 들어갈 수 있는 환경 파일은 커밋하지 않는다.
- `poe test`·`poe coverage`만 커밋된 `.env.example`을 먼저 읽고, 있으면 `.env`, `.env.test` 순서로 값을 덮어쓴다. 다른 poe 작업은 `.env.example`을 읽지 않는다.

## 변경 원칙

- 요청받은 범위만 수정하고 관련 없는 기존 변경은 보존한다.
- 새 동작이나 버그 수정에는 가장 가까운 계층의 테스트를 추가하거나 수정한다.
- 공개 API 요청과 응답은 Pydantic 모델로 정의하고 JSON 필드명과 오류 응답 계약을 유지한다.
- API 스키마나 설명이 바뀌면 `uv run --locked poe openapi`로 `openapi/openapi.json`을 갱신하고 함께 검증한다.
- 이미 적용된 Alembic 마이그레이션은 다시 쓰지 않는다. 스키마 변경에는 새 마이그레이션을 추가한다.

## 빌드와 검증

변경 중에는 관련 테스트를 먼저 실행하고, 커밋 전에는 다음 검증을 모두 통과시킨다.

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked complexipy src --max-complexity-allowed 15 --isolated --no-ignore
uv run --locked ty check
uv run --locked poe lint-imports
uv build
uv run --locked poe coverage
git diff --check
git diff --cached --check
```

- `poe coverage`와 CI는 테스트 후 `scripts/check_coverage.py`로 라인 95%, 브랜치 90% 기준을 각각 반올림 없는 정확한 비율로 판정하고, 하나라도 미달이면 실패한다. 기준값은 이 스크립트에만 둔다.
- `scripts/check_coverage.py`를 단독 실행하면 직전 `--cov` 실행의 `.coverage` 데이터를 읽는다. 데이터나 브랜치 측정이 없으면 실패한다.
- 테스트 일부만 실행할 때는 커버리지 기준이 적용되지 않는 `poe test` 또는 `pytest`를 쓴다.
- complexipy는 기준선 없이 `src`의 모든 함수를 검사하고, 인지 복잡도가 15를 초과하는 함수가 있으면 실패한다. 기준선 파일 `complexipy-snapshot.json`을 만들지 않으며, 검사기와 CI는 이 파일이 있으면 실패한다.
- `poe lint-imports`는 `pyproject.toml`의 `[tool.importlinter]` 계약(ADR 0011 「강제 방법」)으로 BC 사이 방향(labeling > observation > labels > identity), BC 내부 계층, `shared`·`platform`·`entrypoints`의 import 경계를 검사한다(ADR 0015). 새 BC를 추가하면 그 BC의 계약과 BC 사이 `layers` 계약의 자리, BC → `mosemo.entrypoints` 금지 계약(`bounded contexts do not import entrypoints`)의 `source_modules`를 함께 추가한다.
- 엔티티는 BC domain의 dataclass로 두고 BC infrastructure의 `Table`과 imperative mapping으로 영속화한다(ADR 0013). Ruff `TID251`이 `DeclarativeBase`·`mapped_column` 재도입을 막는다.
- Ruff 오류는 `uv run --locked ruff check --fix <path>`로 제한된 범위에서 수정한다.
- 포맷이 필요하면 `uv run --locked ruff format <path>`를 실행한다.
- `requires_db`·`requires_kafka` 마커가 붙은 테스트만 Docker 컨테이너를 실행하므로 Docker가 동작 중이어야 한다. 마커는 컨테이너 fixture 사용 여부에 따라 자동으로 붙는다. 새 컨테이너 fixture를 추가하면 `tests/conftest.py`의 마커 매핑도 함께 추가한다. Docker 없이 실행하려면 `-m "not requires_db and not requires_kafka"`를 쓴다.
- 실행하지 못한 검증이나 실패한 검증은 완료된 것으로 표현하지 않고 이유를 보고한다.

## GitHub 사용

- 저장소·Issue·PR·리뷰·Actions·게시된 파일 등 GitHub의 조회와 변경은 항상 `gh` CLI로 수행한다. API가 필요하면 `gh api`를 사용한다.
- 인증은 대상 GitHub 호스트에 `gh auth login`으로 로그인된 활성 계정을 사용한다. 명령 실행 시 `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN` 환경 변수를 해제하여 `gh`에 저장된 인증을 사용한다. 예: `env -u GH_TOKEN -u GITHUB_TOKEN -u GH_ENTERPRISE_TOKEN -u GITHUB_ENTERPRISE_TOKEN gh api user`.
- 같은 환경에서 `gh auth status`로 대상 호스트의 활성 계정을 확인하고 작업한다. `gh` 실행·인증·권한 문제가 있으면 원인과 필요한 조치를 보고한다.
- 여러 줄 Issue·PR 본문이나 댓글은 임시 파일에 작성하고 `--body-file`로 전달한다.

## 커밋

- 사용자의 검토 또는 명시적 요청 전에는 커밋하지 않는다.
- 한 커밋에는 하나의 논리적 변경만 포함하며 관련 없는 파일을 스테이징하지 않는다.
- 커밋 메시지는 Conventional Commits 형식인 `<type>(<scope>): <description>`을 사용한다.
- `type`은 변경 목적에 맞게 `feat`, `fix`, `refactor`, `test`, `docs`, `build`, `ci`, `chore` 중에서 선택한다.
- 설명은 짧은 영어 명령형으로 작성한다. 예: `docs: add repository agent guidelines`
- 커밋 전 `git status --short`와 스테이징된 diff를 확인한다.

## Engineering Harness

각 Skill은 독립적으로 호출할 수 있다. 아래 순서는 전체 작업 흐름의 안내이며 필수 실행 순서가 아니다. 사용자 요청과 대화에서 필요한 범위·결정·기대 결과를 확인할 수 있으면 선행 Skill 실행이나 PRD·Plan Issue 생성을 강제하지 않는다. 기존 문서·Issue·승인은 해당 범위에 존재할 때 참조하고, 실제로 부족한 정책 결정만 확인한다. 다른 Skill은 필요한 전문 절차의 참조이며, 요청한 작업을 마치면 다음 단계로 자동 진행하지 않는다.

1. [요구사항 탐색](.codex/skills/requirement-discovery/SKILL.md)
2. [제품 명세 작성](.codex/skills/specification/SKILL.md)
3. [엔지니어링 계획](.codex/skills/engineering-planning/SKILL.md): 필요한 도메인·영속성 모델링과 아키텍처 결정을 조율한다.
4. [구현](.codex/skills/implementation/SKILL.md)
5. [검증](.codex/skills/verification/SKILL.md)

[아키텍처 결정 기록(ADR)](.codex/skills/architecture-decision/SKILL.md)은 독립적으로도 사용한다. 각 단계의 절차와 규칙은 해당 Skill을 따른다.

[유비쿼터스 언어](.codex/skills/ubiquitous-language/SKILL.md)는 용어집만 작성·검토·갱신하는 독립 요청이나 다른 단계에서 새로운 업무 개념·의미 충돌을 발견했을 때 사용한다. 요구사항 탐색에서 용어집을 작성·갱신하고 명세에서 의미 일관성을 확인하며, 이후 단계의 발견도 피드백한다. 모든 작업의 필수 선행 단계는 아니다.
