---
type: onboarding
title: Mosemo 서버 빠른 시작
description: 처음 보는 사람을 위한 Mosemo 서버 온보딩과 위키 진입점. 개발 환경 준비, 개발 서버 실행, Docker가 필요한 테스트, 커밋 전 검증, 작업 흐름, 먼저 읽을 문서와 위키 갱신 절차를 안내한다.
tags: [onboarding, quickstart, setup, wiki]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-12T08:09:21.750Z
sources:
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
generated: { by: "claude-code", at: "2026-11-12T08:09:21.750Z" }
---

# Mosemo 서버 빠른 시작

Mosemo 서버는 Device가 관찰한 활동을 받아 날짜별 관찰 타임라인과 라벨별 활동 시간을 제공하는 FastAPI·SQLAlchemy·Kafka 기반 백엔드다. 이 위키는 저장소의 원본 문서와 코드에서 만든 요약이다. **위키와 원본이 다르면 원본이 맞다.** 각 페이지는 근거가 된 원본으로 링크한다.

## 1. 개발 환경

- Python 3.14 이상([.python-version](../.python-version), `pyproject.toml`의 `requires-python`)과 [uv](https://docs.astral.sh/uv/)를 쓴다.
- 잠금 파일을 바꾸지 않고 설치한다.

  ```sh
  uv sync --locked
  uv run pre-commit install
  ```

- `.env.example`을 `.env`로 복사해 값을 채운다. `.env`, `.env.test` 같은 비밀값 파일은 커밋하지 않는다. 프로세스별로 필요한 환경 변수는 [README.md](../README.md) 「Environment variables per process」를 본다.

## 2. 로컬 의존 서비스와 개발 서버

[docker-compose.yml](../docker-compose.yml)에 PostgreSQL(`db`)과 단일 노드 Kafka(`kafka`, 토픽 생성용 `kafka-init`)가 있다.

```sh
docker compose up -d db
uv run --locked poe migration-dev          # alembic upgrade head
uv run --env-file .env fastapi dev         # 개발 API 서버
```

- Kafka를 띄우는 방법과 `BATCH_*` 설정은 [README.md](../README.md) 「Local Kafka」를 본다.
- 활동 배치 Consumer는 `poe batch-consumer`, 배치 job은 `poe batch-jobs serve`(상시) 또는 `poe batch-jobs run JOB_NAME`(한 번)으로 실행한다. 각 프로세스의 역할은 [영속성·메시징·배치 실행](architecture/persistence-and-messaging.md)을 본다.

## 3. 테스트

- 일부 테스트: `uv run --locked poe test -q tests/unit/...`(커버리지 기준 없음)
- `requires_db`(PostgreSQL)·`requires_kafka`(Kafka) 마커가 붙은 테스트는 testcontainers로 컨테이너를 띄우므로 **Docker가 동작 중이어야 한다.** 마커는 fixture를 보고 자동으로 붙는다.
- Docker 없이 돌리려면 `-m "not requires_db and not requires_kafka"`를 붙인다.

자세한 분류와 위치는 [테스트와 검증](development/testing-and-verification.md)을 본다.

## 4. 커밋 전 검증

[AGENTS.md](../AGENTS.md) 「빌드와 검증」의 명령을 모두 통과시킨다.

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

`bash .codex/skills/verification/scripts/validate.sh`는 이 명령과 문서·아키텍처·규칙 검사를 한 번에 돌린다. 커버리지는 라인 95%, 브랜치 90% 이상이어야 한다. API 스키마가 바뀌면 `uv run --locked poe openapi`로 `openapi/openapi.json`을 갱신한다.

## 5. 작업 흐름(Engineering Harness)

요구사항 탐색 → 제품 명세 → 엔지니어링 계획 → 구현 → 검증 순서의 Skill이 `.codex/skills/`에 있다. 순서는 안내일 뿐이며 각 Skill은 독립적으로 쓸 수 있다. 구현 뒤에는 변경 파일에 따라 `code-reviewer`·`security-auditor`·`domain-reviewer` 리뷰를 받는다. 자세한 내용은 [Engineering Harness 작업 흐름](development/engineering-harness.md)을 본다.

## 6. 먼저 읽을 문서

1. [ARCHITECTURE.md](../ARCHITECTURE.md): 패키지 구조와 의존 규칙
2. [AGENTS.md](../AGENTS.md): 저장소 작업 규칙, [src/mosemo/AGENTS.md](../src/mosemo/AGENTS.md)(코드), [tests/AGENTS.md](../tests/AGENTS.md)(테스트)
3. [docs/index.md](../docs/index.md): 용어집, 요구사항, PRD, ADR 목록
4. [docs/ubiquitous-language.md](../docs/ubiquitous-language.md): 도메인 용어

## 위키 페이지 안내

| 알고 싶은 것 | 페이지 |
| --- | --- |
| BC·계층·진입점과 import 경계 | [현재 구조](architecture/current-structure.md) |
| 저장·트랜잭션·이벤트·Kafka·배치 job | [영속성·메시징·배치 실행](architecture/persistence-and-messaging.md) |
| 지금 유효한 ADR과 대체 관계 | [유효한 결정](decisions/active-decisions.md) |
| 기능 범위·API·도메인 용어 | [제품 기능과 도메인 용어](domain/product-overview.md) |
| 테스트 분류·마커·검증·CI | [테스트와 검증](development/testing-and-verification.md) |
| Skill 흐름·리뷰어·커밋 규칙 | [Engineering Harness 작업 흐름](development/engineering-harness.md) |

## 위키 갱신 절차

- `openwiki/` 아래 생성물은 손으로 고치지 않는다. main 병합 후 Aico가 `openwiki/update` 브랜치의 문서 PR로 갱신한다.
- 위키의 범위와 우선순위는 [openwiki/INSTRUCTIONS.md](INSTRUCTIONS.md)로만 바꾼다. 지시서만 바뀐 병합도 강제 갱신으로 반영된다.
- 내용이 틀렸다면 원본 문서나 코드를 고치거나 지시서를 고친다.
