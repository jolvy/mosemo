---
type: workflow
title: Engineering Harness 작업 흐름
description: 요구사항 탐색부터 검증까지의 저장소 Skill 흐름, ADR·유비쿼터스 언어 Skill, 리뷰어 에이전트와 호출 조건, CLAUDE.md shim과 GitHub·커밋 규칙을 정리한다.
tags: [harness, skills, review, agents, workflow]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-20T11:18:18.429Z
sources:
  - id: openwiki-source-1fe463fcf07912e5cdbb5a91
    resource: repo://.claude/settings.json
  - id: openwiki-source-a01602e6c78f10583af45ce4
    resource: repo://.codex/skills/implementation/SKILL.md
  - id: openwiki-source-2fa4aa380ad46b14f938979f
    resource: repo://.codex/skills/requirement-discovery/SKILL.md
  - id: openwiki-source-fb4b9ae1c4cd229650dbdc2f
    resource: repo://.codex/skills/specification/SKILL.md
  - id: openwiki-source-d0be9d092ade01b9dc055895
    resource: repo://.codex/skills/verification/scripts/check_openwiki_paths.py
  - id: openwiki-source-9e13001a26410d98eff26719
    resource: repo://.codex/skills/verification/scripts/sync_openwiki_update.py
  - id: openwiki-source-04b0cb290c685d4ed48b8c0a
    resource: repo://.codex/skills/verification/scripts/validate_docs.py
  - id: openwiki-source-13149af8c20ff341c7c2ba3a
    resource: repo://.github/workflows/openwiki-paths.yml
  - id: openwiki-source-6d4b4e707b8d60b6ccfa3425
    resource: repo://.github/workflows/openwiki-update.yml
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-8a9d63bf55880a1423bc3a48
    resource: repo://tests/unit/harness/test_claude_shims.py
generated: { by: "claude-code", at: "2026-11-19T07:46:46.571Z" }
---

# Engineering Harness 작업 흐름

이 저장소는 사람과 코딩 에이전트가 같은 기준으로 일하도록 Skill·리뷰어 에이전트·검사 스크립트를 함께 둔다. 원본은 루트 [AGENTS.md](../../AGENTS.md) 「Engineering Harness」와 각 Skill 문서다. 이 페이지와 다르면 원본이 맞다.

## Skill 흐름

Skill은 `.codex/skills/` 아래에 있고, `.claude/skills`는 이 폴더를 가리키는 심볼릭 링크다. 그래서 Codex와 Claude Code가 같은 Skill을 본다.

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
graph LR
  R[requirement-discovery<br/>요구사항 탐색] --> S[specification<br/>제품 명세]
  S --> P[engineering-planning<br/>엔지니어링 계획]
  P --> I[implementation<br/>구현]
  I --> V[verification<br/>검증]
```

| Skill | 언제 쓰나 | 결과물 |
| --- | --- | --- |
| [requirement-discovery](../../.codex/skills/requirement-discovery/SKILL.md) | 신규 기능·변경 요청에서 도메인 사실·정책·인수 조건을 찾을 때 | `docs/requirements/` |
| [specification](../../.codex/skills/specification/SKILL.md) | 확정된 요구사항을 PRD로 만들 때 | `docs/prd/` |
| [engineering-planning](../../.codex/skills/engineering-planning/SKILL.md) | 도메인·영속성 설계를 조율하고 실행 계획이나 GitHub Plan Issue를 쓸 때 | Plan Issue |
| [implementation](../../.codex/skills/implementation/SKILL.md) | 요청한 변경이나 승인된 계획을 구현하고 테스트·검증·리뷰까지 마칠 때 | 코드·테스트 |
| [verification](../../.codex/skills/verification/SKILL.md) | 문서 형식·품질 게이트·테스트 결과를 확인할 때 | 검증 결과 |

- 순서는 전체 흐름의 안내일 뿐 필수 실행 순서가 아니다. 필요한 범위·결정·기대 결과가 요청과 대화로 확인되면 선행 Skill이나 PRD·Plan Issue 생성을 강제하지 않는다.
- 독립 Skill: [architecture-decision](../../.codex/skills/architecture-decision/SKILL.md)(ADR 작성·대체), [ubiquitous-language](../../.codex/skills/ubiquitous-language/SKILL.md)(용어집 [docs/ubiquitous-language.md](../../docs/ubiquitous-language.md) 갱신), [pull-request](../../.codex/skills/pull-request/SKILL.md)(PR 본문 템플릿), [agent-metrics-analysis](../../.codex/skills/agent-metrics-analysis/SKILL.md)(에이전트 지표 보고서).
- Plan Issue가 있으면 구현은 그 본문을 계획의 유일한 원본으로 삼는다.

## PRD·ADR 유효 여부 확인

- PRD 앞부분 `status`는 초안이면 `draft`, 사용자 승인 뒤 `accepted`다. 다른 PRD가 기획을 통째로 대신하면 옛 PRD를 `status: superseded`와 `superseded_by`로 바꾸고 본문은 지우지 않는다.
- [AGENTS.md](../../AGENTS.md) 「기획·결정 확인」은 `docs/prd/`·`docs/adr/`를 열거하거나 검색하기 전에 먼저 [유효한 기획](../decisions/active-prds.md)·[유효한 결정](../decisions/active-decisions.md)에서 현재 유효한 문서를 확인하고 링크된 원본으로 확정하라고 한다. `superseded` 문서는 현재 기획·결정으로 인용하지 않고 `superseded_by`를 따라가며, 위키와 다르면 원본이 기준이다.

## 리뷰어 에이전트

리뷰어 정의는 `.claude/agents/`에 있고 모두 읽기 전용이다. [implementation Skill](../../.codex/skills/implementation/SKILL.md) 「리뷰」가 변경 파일 경로로 호출 여부를 정한다.

| 리뷰어 | 호출 조건(요약) |
| --- | --- |
| `code-reviewer` | Python 파일(`*.py`)이나 `migrations/` 아래 파일을 바꿀 때 |
| `security-auditor` | `src/` 아래 `.md`가 아닌 파일(BC `domain/`만 바꾼 경우 제외, identity domain은 포함), 또는 `pyproject.toml`·`uv.lock`·`docker-compose.yml`·`.github/workflows/`·`scripts/` 등 설정·운영 파일을 바꿀 때 |
| `domain-reviewer` | `src/mosemo/*/domain/`·`application/`을 바꾸거나 PRD가 정한 동작을 추가·수정할 때 |
| `plan-reviewer` | Plan Issue 초안을 등록 전에 검토할 때 |

어느 조건에도 해당하지 않으면 리뷰어를 부르지 않고, 보고에 그 이유와 변경 파일 목록을 적는다. 해당 여부가 애매하면 부른다.

## 에이전트 지침 파일

- 루트 [AGENTS.md](../../AGENTS.md), [src/mosemo/AGENTS.md](../../src/mosemo/AGENTS.md), [tests/AGENTS.md](../../tests/AGENTS.md)가 계층별 작업 규칙이다.
- 같은 폴더의 `CLAUDE.md`는 내용이 `@AGENTS.md` 한 줄인 Claude Code용 shim이다. `AGENTS.md`를 추가하면 shim도 함께 추가해야 하며 `tests/unit/harness/test_claude_shims.py`가 이를 검사한다.
- 루트 AGENTS.md 끝의 `OPENWIKI:START`~`OPENWIKI:END` 블록은 OpenWiki가 관리한다.
- [.claude/settings.json](../../.claude/settings.json)의 `PreToolUse` hook이 Read·Edit·Write·MultiEdit·NotebookEdit 호출에서 파일명이 `.env`이거나 `.env.`로 시작하는 파일(`.env.example` 제외)을 막는다(종료 코드 2). 비밀값이 있을 수 있어 설정 키는 `.env.example`로 확인한다.
- `validate_docs.py`는 `docs/requirements/`·`docs/adr/`에 더해 `docs/prd/` 문서도 앞부분 `status`(`draft`·`proposed`·`accepted`·`superseded`)가 있는지 검사한다.

## OpenWiki 생성물 보호와 갱신

- `openwiki/` 아래 페이지는 생성물이다. 범위와 언어·필수 페이지는 사람이 쓰는 지시서 [openwiki/INSTRUCTIONS.md](../INSTRUCTIONS.md)로만 바꾼다.
- 생성물은 main 병합 후 [OpenWiki update 워크플로](../../.github/workflows/openwiki-update.yml)가 Claude Code로 갱신해 같은 저장소의 `openwiki/update` 브랜치 문서 PR로 올리고 auto-merge한다. 위키 PR 병합은 워크플로를 다시 실행하지 않으며, 지시서만 바뀐 병합은 `paths` 필터로 다시 넣어 갱신한다.
- 워크플로는 생성 job과 게시 job을 다른 러너로 나눈다. 생성 job은 `main`에서만 돌고, Claude Code에 `openwiki/**` 쓰기와 읽기 전용 git 명령만 허용한 채 위키를 만들어 artifact로 올린다. 모델이 쓴 결과가 PR 토큰과 섞이지 않게 하려는 분리다.
- 게시 job은 최신 main을 checkout하고 [sync_openwiki_update.py](../../.codex/skills/verification/scripts/sync_openwiki_update.py)로 artifact를 검증한 뒤 반영한다. 검증은 일반 파일만 허용(symlink 금지), 확장자 `.md`·`.json`, `.last-update.json`의 `status`가 `complete`, 토큰 형태 문자열(`sk-ant-`·`github_pat_`·`gh?_` 접두어 뒤 본문) 없음이다. 지시서 `INSTRUCTIONS.md`는 생성 결과에 있어도 반영하지 않고, 기존 생성물을 지우고 채우므로 삭제된 페이지도 반영된다. `.last-update.json`만 바뀐 갱신은 PR을 만들지 않는다.
- [.github/workflows/openwiki-paths.yml](../../.github/workflows/openwiki-paths.yml)의 `OpenWiki paths` job이 일반 PR의 생성물 변경을 막는다. `pull_request_target`으로 base 브랜치의 워크플로·스크립트를 실행하므로 PR이 검사를 끄거나 약하게 만들 수 없고, PR head는 checkout하지 않고 commit 데이터로만 받는다.
- 검사 스크립트 [check_openwiki_paths.py](../../.codex/skills/verification/scripts/check_openwiki_paths.py)는 `git diff --name-only --no-renames -z`로 변경 경로를 구한다. 같은 저장소의 `openwiki/update` 브랜치 PR은 `openwiki/` 아래 생성물(`INSTRUCTIONS.md` 제외, `.md`·`.json`)이 아닌 경로가 하나라도 있으면 실패하고, 그 밖의 PR은 대소문자를 무시해 `openwiki/`로 시작하면서 `openwiki/INSTRUCTIONS.md`가 아닌 경로가 있으면 실패한다. `--no-renames`는 `openwiki/` 밖으로 옮긴 파일이 빠져나가지 않게 한다.

## GitHub와 커밋

- GitHub 작업은 항상 `gh` CLI로 하고, `GH_TOKEN` 등 토큰 환경 변수를 해제해 `gh`에 저장된 인증을 쓴다.
- 여러 줄 본문은 임시 파일에 쓰고 `--body-file`로 넘긴다.
- 커밋은 사용자 검토나 명시적 요청 뒤에만 하며, Conventional Commits(`<type>(<scope>): <description>`, 영어 명령형)를 쓴다.

## 관련 페이지

- [빠른 시작](../quickstart.md)
- [테스트와 검증](testing-and-verification.md)
- [유효한 기획](../decisions/active-prds.md)
