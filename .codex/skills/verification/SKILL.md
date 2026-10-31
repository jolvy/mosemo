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
- `uv run --locked python .codex/skills/verification/scripts/validate_architecture.py`: 모델·저장소에서 FastAPI/Starlette 및 Mosemo router 모듈 참조를 금지하고, ADR 0001에 따라 exceptions/responses에서 openapi 참조를 금지한다. 절대·상대·함수 내부 import를 AST로 검사하며 기존 ORM의 SQLAlchemy 사용은 허용한다.
- `uv run --locked python .codex/skills/verification/scripts/validate_conventions.py`: 현재 시각 처리와 검사 억제 규칙을 검사한다. 테스트의 `clock=` lambda와 `get_clock` 재정의의 lambda 반환 lambda, `src`·`tests`·`scripts`·`migrations`·`.codex` 주석의 규칙 미지정 `noqa`·`ruff: noqa`·TID251/DTZ 억제, `pyproject.toml`의 TID251/DTZ/`ALL` 무시와 `exclude`, 다른 Ruff 설정 파일, `src`의 `__init__`에서 키워드 전용 필수가 아닌 `clock` 매개변수를 금지한다. 위반마다 파일·라인·규칙 ID와 수정 안내를 출력한다.
- `bash .codex/skills/verification/scripts/validate.sh`: 문서·구조·코드 규칙 검사와 Ruff·포맷·타입·빌드·전체 테스트·diff 검사를 실행한다. 스크립트 위치에서 저장소 루트를 찾아 실행한다.

검사기 회귀 테스트는 `tests/unit/harness/`에 둔다. 문서·구조 검사와 회귀 테스트는 Git 훅과 CI에서도 실행한다. 로컬 훅 설치는 `uv run --locked pre-commit install --hook-type pre-commit --hook-type pre-push`로 수행한다. 훅은 우회 가능하며 원격 필수 체크 설정은 별도 확인한다.

정적 검사 통과는 승인 진위, 외부 링크 실존, 의미적 요구사항의 완전성이나 동적 import의 안전성을 보장하지 않는다. 검사 규칙을 변경해 위반을 숨기지 않는다.
