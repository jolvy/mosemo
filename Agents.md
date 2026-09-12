# Mosemo

## 프로젝트 구조

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

## 커밋

- 사용자의 검토 또는 명시적 요청 전에는 커밋하지 않는다.
- 한 커밋에는 하나의 논리적 변경만 포함하며 관련 없는 파일을 스테이징하지 않는다.
- 커밋 메시지는 Conventional Commits 형식인 `<type>(<scope>): <description>`을 사용한다.
- `type`은 변경 목적에 맞게 `feat`, `fix`, `refactor`, `test`, `docs`, `build`, `ci`, `chore` 중에서 선택한다.
- 설명은 짧은 영어 명령형으로 작성한다. 예: `docs: add repository agent guidelines`
- 커밋 전 `git status --short`와 스테이징된 diff를 확인한다.
