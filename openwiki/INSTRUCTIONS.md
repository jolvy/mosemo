# OpenWiki 지시서

이 파일은 사람이 쓰는 지시서다. `openwiki/` 아래 다른 파일은 OpenWiki 생성물이다.

## 언어

- 위키는 한국어로 쓴다.
- 코드 식별자·경로·명령은 원문 그대로 둔다.

## 필수 페이지

- 온보딩 겸 진입점: `openwiki/quickstart.md`
- 현재 구조: BC·계층·진입점·영속성·메시징의 지금 모습
- 유효한 결정: 현재 효력이 있는 ADR과 대체 관계
- 유효한 기획: 현재 효력이 있는 PRD와 대체 관계

유효한 결정·기획 페이지는 `openwiki/decisions/` 아래에 둔다. `AGENTS.md` 「기획·결정 확인」이 이 폴더를 진입점으로 쓴다.

나머지 페이지 구성은 OpenWiki가 정한다.

## 근거 우선순위

1. `ARCHITECTURE.md`
2. `AGENTS.md` 계층(루트, `src/mosemo/AGENTS.md`, `tests/AGENTS.md` 등)
3. `docs/adr/`
4. `docs/prd/`, `docs/requirements/`
5. `docs/ubiquitous-language.md`
6. `pyproject.toml`의 import-linter 계약(`[tool.importlinter]`)
7. 코드·테스트·`migrations/`

## 문서 정책

- 원본 문서가 기준이다. 위키와 원본이 다르면 원본이 맞다. 위키 페이지는 근거가 된 원본 문서·코드로 링크한다.
- 생성물은 손으로 고치지 않는다. 범위는 이 지시서로만 바꾼다.
- 유효한 결정과 기획은 ADR·PRD 앞부분의 `status`로 판단한다.

## ADR 링크 규칙

- 유효한 결정 페이지는 `status: accepted`인 ADR마다 `docs/adr/` 아래 파일로 Markdown 링크한다(예: `[ADR 0011](../docs/adr/0011-split-bounded-contexts-logically-with-layered-packages.md)`).
- 부분 대체(0004와 0007·0008, 0001과 0014, 0010과 0011, 0011과 0015)와 전체 대체(0002 → 0004)는 두 ADR 파일 링크를 같은 페이지에 둔다.
- `status: superseded`인 ADR은 유효 목록에서 빼고 앞부분의 `superseded_by` ADR로 안내한다.

## PRD 링크 규칙

- 유효한 기획 페이지는 `status: accepted`인 PRD마다 `docs/prd/` 아래 파일로 Markdown 링크하고, 한두 문장으로 범위를 요약한다.
- `status: draft`인 PRD는 확정되지 않은 기획으로 따로 표시한다.
- `status: superseded`인 PRD는 유효 목록에서 빼고 앞부분의 `superseded_by` PRD로 안내한다.

## 온보딩에 담을 것

- Python 3.14와 `uv sync --locked`
- 개발 서버 실행(`uv run --env-file .env fastapi dev`)
- Docker가 필요한 테스트 마커(`requires_db`, `requires_kafka`)
- `AGENTS.md` 「빌드와 검증」 명령
- Engineering Harness Skill 흐름(요구사항 탐색 → 제품 명세 → 엔지니어링 계획 → 구현 → 검증)
- 먼저 읽을 문서(유효한 기획·결정 페이지 포함)

## 위키 갱신 절차

- 생성물은 main 병합 후 `OpenWiki update` 워크플로(`.github/workflows/openwiki-update.yml`)가 Claude Code로 갱신해 `openwiki/update` 브랜치의 문서 PR로 올리고 auto-merge하며, 손으로 고치지 않는다.
- 지시서만 바뀐 병합도 강제 갱신으로 반영된다.
