# Mosemo

## 프로젝트 구조

- 디렉터리와 모듈 책임의 탐색 시작점: [ARCHITECTURE.md](ARCHITECTURE.md)
- 문서 탐색 시작점: [docs/index.md](docs/index.md)

- 애플리케이션 코드: `src/mosemo/` (FastAPI, Pydantic, SQLAlchemy)
- 테스트: `tests/unit/`, `tests/component/`, `tests/integration/` (pytest)
- 데이터베이스 마이그레이션: `migrations/` (Alembic)
- 공개 API 스냅샷: `openapi/openapi.json`
- OpenAPI 내보내기 스크립트: `scripts/export_openapi.py`

## 개발 환경

- Python 3.14 이상과 `uv`를 사용한다.
- 잠금 파일을 변경하지 않는 설치는 `uv sync --locked`로 실행한다.
- 로컬 개발 서버는 `uv run --env-file .env fastapi dev`로 실행한다.
- `.env`, `.env.test` 등 비밀값이 들어갈 수 있는 환경 파일은 커밋하지 않는다.

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
uv run --locked ty check
uv build
uv run --locked poe test
git diff --check
git diff --cached --check
```

- Ruff 오류는 `uv run --locked ruff check --fix <path>`로 제한된 범위에서 수정한다.
- 포맷이 필요하면 `uv run --locked ruff format <path>`를 실행한다.
- 통합 테스트는 Docker에서 PostgreSQL 컨테이너를 실행하므로 Docker가 동작 중이어야 한다.
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
