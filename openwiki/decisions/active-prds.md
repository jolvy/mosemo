---
type: decision-index
title: 유효한 기획
description: docs/prd의 PRD 중 앞부분 status가 accepted인 유효한 기획 목록을 원본 링크와 범위 요약으로 정리하고, draft·superseded PRD를 다루는 규칙을 안내한다.
tags: [prd, planning, supersession]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-22T12:16:52.428Z
sources:
  - id: openwiki-source-fb4b9ae1c4cd229650dbdc2f
    resource: repo://.codex/skills/specification/SKILL.md
  - id: openwiki-source-ea10ab99eb10f44aef738f44
    resource: repo://docs/prd/activity-batches.md
  - id: openwiki-source-25c559c5d2e9eb36069e3dc8
    resource: repo://docs/prd/activity-capture.md
  - id: openwiki-source-d8bf75df712257f7d0c8d961
    resource: repo://docs/prd/activity-label-package-structure.md
  - id: openwiki-source-8b862b573c95d1ffd17bb171
    resource: repo://docs/prd/activity-labeling.md
  - id: openwiki-source-855ffdc4bc4914301090ca25
    resource: repo://docs/prd/device-registration.md
  - id: openwiki-source-21f0dc39b7ec888bdabede7c
    resource: repo://docs/prd/focus-sessions.md
  - id: openwiki-source-0dbe9c015eddcfd9fd71ce78
    resource: repo://docs/prd/observation-timeline.md
generated: { by: "claude-code", at: "2026-11-22T11:39:33.794Z" }
---

# 유효한 기획

유효 여부는 각 PRD 앞부분의 `status`로 판단한다. `accepted`만 유효 목록에 넣고, `draft`는 확정되지 않은 기획으로 따로 표시하며, `superseded`는 목록에서 빼고 `superseded_by`가 가리키는 PRD로 안내한다. 이 페이지와 PRD 원문이 다르면 원문이 맞다. 작성 절차는 [specification Skill](../../.codex/skills/specification/SKILL.md)을 따른다.

## 유효한 PRD 목록 (`status: accepted`)

| PRD | 범위 |
| --- | --- |
| [활동 관찰과 개인정보 보호](../../docs/prd/activity-capture.md) | 현재 포커스한 대상의 관찰값을 활동 레코드로 만든다. 식별 정보는 서버 전송 전에 클라이언트에서 거르고, 앱·창·웹의 관찰 상태를 독립적으로 표현한다. |
| [활동 배치 수락과 비동기 처리](../../docs/prd/activity-batches.md) | 한 Device의 활동 레코드를 배치로 수락하고 DB 처리는 따로 수행한다. 전달 수락·원본별 거절·DB 반영을 구분하고 재전송·순번 누락·역순 도착에도 중복 반영하지 않는다. 서버 수락 시각 기준 24시간 밖에서 관찰한 원본이 하나라도 있으면 배치 전체를 422로 거절한다. 2026-11-22 Issue #349로 처리 실패 요구를 확정했다: Consumer는 영구 실패·분류에 없는 예외를 건너뛰고(원문 미보관, 같은 Device 뒤 배치 보류 없음), 일시 실패는 최대 5회(1/2/4/8초) 재시도한 뒤 ACK 없이 끝낸다. 건너뛴 배치는 로그의 Kafka 위치로 찾으며 클라이언트에는 알리지 않는다. 2026-11-22 Issue #350으로 DB가 저장할 수 없는 값(문자열 값의 NUL 문자, 2^63 − 1을 넘는 `sequence`, −1440~1440 밖의 `utcOffsetMinutes`)을 422 `INVALID_ARGUMENT`로 거절하도록 했고, 이 422를 받은 클라이언트는 같은 내용을 재시도하지 않고 `details`의 `loc`이 가리키는 원본만(순번을 가리키지 않으면 배치 전체를) 버린다. |
| [날짜별 관찰 타임라인 API](../../docs/prd/observation-timeline.md) | 계정 시간대의 날짜를 지정해 하루 전체의 관찰 타임라인을 조회한다. 모든 Device의 원본을 하나의 관찰 순서로 해석해 활동 구간과 수집 공백을 저장형 projection으로 제공한다. |
| [개인화 활동 라벨링](../../docs/prd/activity-labeling.md) | 관찰 사실과 별도로 사용자별 라벨 목록, AI 라벨 제안, 사용자 확정값을 관리한다. 대시보드 합계에는 사용자 확정값만 넣는다. |
| [활동 라벨 모듈의 패키지 재배치](../../docs/prd/activity-label-package-structure.md) | 관찰·라벨 목록·확정·AI 제안 흐름을 책임별 패키지로 재배치한다. 관찰 사실은 `activities`, 라벨 기능은 `activity_labels`에 둔다. |
| [Device 등록 API](../../docs/prd/device-registration.md) | `Device`를 계정에 귀속된 등록된 앱 설치로 정의한다. UUID `Idempotency-Key`와 함께 `POST /api/v1/devices`를 호출하면 UUIDv7 `deviceId`를 발급한다. |
| [집중 세션 API](../../docs/prd/focus-sessions.md) | 의도한 집중 작업을 관찰 활동과 구분해 생성·완료·날짜별 조회하고, 관찰 시점에 진행 중인 세션을 활동 레코드와 연결한다. 완료 후 늦게 도착한 관찰은 수락 기간(24시간) 안에서만 받는다. |

## 확정되지 않은 기획 (`status: draft`)

지금 `docs/prd/`에는 `draft` PRD가 없다. 새 PRD는 초안일 때 `draft`로 쓰고 사용자 승인 뒤 `accepted`로 바꾼다.

## 대체된 기획 (`status: superseded`)

지금 `superseded` PRD는 없다. 다른 PRD가 기획을 통째로 대신하면 옛 PRD를 `status: superseded`와 `superseded_by: <새 PRD 파일명>`으로 바꾸고 본문은 지우지 않는다. 그런 PRD는 현재 기획으로 인용하지 않고 `superseded_by` 문서를 따른다.

## 관련 페이지

- [유효한 결정](active-decisions.md): 같은 방식으로 정리한 유효한 ADR 목록
- [빠른 시작](../quickstart.md)
- [Engineering Harness 작업 흐름](../development/engineering-harness.md)
