---
type: workflow
title: 테스트와 검증
description: 테스트 분류(Unit·Integration·E2E)와 위치, Docker가 필요한 requires_db·requires_kafka 마커, poe 작업, 커밋 전 검증 명령과 커버리지 기준, CI Quality job 단계를 정리한다.
tags: [testing, pytest, coverage, ci, verification]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-12T08:09:21.750Z
sources:
  - id: openwiki-source-8fc10a4767cd01fd40612574
    resource: repo://.codex/skills/verification/scripts/validate.py
  - id: openwiki-source-81b78b1a4f671cb4cd88f0ff
    resource: repo://.codex/skills/verification/scripts/validate.sh
  - id: openwiki-source-164e2da859b5277df81c7d94
    resource: repo://.github/workflows/ci.yml
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-1c9ddf72032c2bd4da068cd8
    resource: repo://scripts/check_coverage.py
  - id: openwiki-source-f0a6e7dc03522b2682f88655
    resource: repo://tests/conftest.py
  - id: openwiki-source-d5a25b3d746855d816062422
    resource: repo://tests/integration/conftest.py
generated: { by: "claude-code", at: "2026-11-12T08:09:21.750Z" }
---

# 테스트와 검증

원본은 [tests/AGENTS.md](../../tests/AGENTS.md)(테스트 분류·위치)와 루트 [AGENTS.md](../../AGENTS.md) 「빌드와 검증」이다. 이 페이지와 다르면 원본이 맞다.

## 테스트 분류와 위치

| 분류 | 위치 | 기준 |
| --- | --- | --- |
| Unit | `tests/unit/{패키지}/` | 하나의 논리적 단위(SUT)의 동작. 복잡한 의존성은 Fake·Mock·Stub으로 격리할 수 있다 |
| Integration | `tests/integration/{패키지}/` | 실제 구성요소 사이 연동, 또는 컴포넌트를 공개 인터페이스(HTTP·메시지·명령어·신호)로 호출하는 검증. Repository·프로세스 수명주기 테스트 포함 |
| E2E | `tests/e2e/`(첫 테스트를 쓸 때 만든다) | PRD의 핵심 업무 시나리오를 띄운 서버 프로세스에 실제 HTTP로 검증 |

- `{패키지}`는 SUT가 있는 `src/mosemo`의 최상위 패키지(`identity`, `labels`, `observation`, `labeling`, `shared`, `platform`, `entrypoints`)다. 예외로 저장소 도구 테스트 `harness`, `scripts`, `migrations`, `support`가 있다.
- 공유 테스트 코드는 `tests/support/`에 둔다.
- 구조 규칙은 `tests/unit/harness/test_tests_layout.py`가 강제한다.

## Docker가 필요한 테스트 마커

- `requires_db`(실제 PostgreSQL)와 `requires_kafka`(실제 Kafka)는 [tests/conftest.py](../../tests/conftest.py)가 fixture 사용 여부를 보고 자동으로 붙인다. 손으로 붙이지 않는다.
- 컨테이너는 testcontainers로 띄운다([tests/integration/conftest.py](../../tests/integration/conftest.py)). 그래서 이 마커가 붙은 테스트를 돌릴 때는 Docker가 동작 중이어야 한다.
- Docker 없이 돌리려면 `-m "not requires_db and not requires_kafka"`를 붙인다.
- 새 컨테이너 fixture를 추가하면 `tests/conftest.py`의 `CONTAINER_MARKERS` 매핑도 함께 추가한다.

## poe 작업

| 작업 | 하는 일 |
| --- | --- |
| `poe test` | `pytest -v`. 커버리지 기준 없이 일부 테스트를 돌릴 때 쓴다 |
| `poe coverage` | `pytest --cov` 뒤 `scripts/check_coverage.py`로 기준을 판정 |
| `poe lint-imports` | import-linter 계약 검사 |
| `poe openapi` | `openapi/openapi.json` 스냅샷 갱신 |
| `poe ci` | `act`로 pull request Quality job을 로컬에서 실행 |

`poe test`·`poe coverage`만 `.env.example`을 먼저 읽고, 있으면 `.env`, `.env.test` 순서로 덮어쓴다.

## 커밋 전 검증

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

- 한 번에 돌리려면 `bash .codex/skills/verification/scripts/validate.sh`를 쓴다. 위 명령에 문서·아키텍처·코드 규칙 검사(`validate_docs.py`, `validate_architecture.py`, `validate_conventions.py`)를 더해 실행하고 마지막 줄에 `VERIFY_RESULT` JSON으로 판정한다.
- 커버리지 기준은 라인 95%, 브랜치 90%이며 반올림 없이 판정한다. 기준값은 [scripts/check_coverage.py](../../scripts/check_coverage.py)에만 둔다.
- complexipy는 기준선 파일 없이 `src`의 모든 함수를 검사하고 인지 복잡도 15를 넘으면 실패한다.
- API 스키마나 설명이 바뀌면 `uv run --locked poe openapi`로 스냅샷을 갱신한다.

## CI

[.github/workflows/ci.yml](../../.github/workflows/ci.yml)의 `Quality` job이 pull request와 main push에서 돈다. 순서는 문서·아키텍처·코드 규칙 검사 → complexipy → Ruff lint·format → ty → import 계약 → OpenAPI 스냅샷 테스트 → 전체 테스트(`pytest --cov`) → 커버리지 기준 판정이다.

## 관련 페이지

- [빠른 시작](../quickstart.md)
- [Engineering Harness 작업 흐름](engineering-harness.md)
