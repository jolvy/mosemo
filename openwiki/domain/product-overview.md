---
type: domain
title: 제품 기능과 도메인 용어
description: PRD·요구사항·용어집으로 본 Mosemo 서버의 기능 범위(Device 등록, 활동 관찰과 배치 전달, 관찰 타임라인, 집중 세션, 라벨링)와 핵심 용어, 각 기능을 맡는 BC와 공개 API를 정리한다.
tags: [domain, prd, glossary, api]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-12T08:09:21.750Z
sources:
  - id: openwiki-source-8e9d01622c02cac4ba03a4c8
    resource: repo://docs/adr/0005-separate-observation-and-personal-label-confirmation.md
  - id: openwiki-source-e93406604bf76d0aeb28f3eb
    resource: repo://docs/ubiquitous-language.md
  - id: openwiki-source-4f7e1a54ac18078c53e2bab7
    resource: repo://src/mosemo/entrypoints/api/routes.py
generated: { by: "claude-code", at: "2026-11-12T08:09:21.750Z" }
---

# 제품 기능과 도메인 용어

Mosemo는 계정에 등록된 앱 설치(Device)가 관찰한 활동을 받아 날짜별 관찰 타임라인으로 보여 주고, 사용자가 그 활동에 라벨을 붙여 집계하게 하는 서비스다. 도메인 언어의 핵심은 **관찰한 사실**과 **그 사실에 대한 후속 해석**을 구분하는 것이다([docs/ubiquitous-language.md](../../docs/ubiquitous-language.md)).

용어의 원본은 용어집, 기능 요구의 원본은 [docs/prd/](../../docs/prd/)와 [docs/requirements/](../../docs/requirements/), 공개 API 계약의 원본은 [openapi/openapi.json](../../openapi/openapi.json)이다. 이 페이지와 다르면 원본이 맞다.

## 기능과 담당 BC

| 기능 | 담당 BC | 공개 API(`/api/v1` 아래) | 원본 문서 |
| --- | --- | --- | --- |
| 로그인·토큰, 내 계정 | `identity` | `GET /auth/kakao/login`, `GET /auth/kakao/callback`, `POST /auth/token`, `GET /accounts/me` | — |
| Device 등록 | `identity` | `POST /devices` | [device-registration.md](../../docs/prd/device-registration.md) |
| 활동 관찰과 개인정보 보호 | `observation` | (배치 전달로 수집) | [activity-capture.md](../../docs/prd/activity-capture.md) |
| 활동 배치 수락과 비동기 처리 | `observation` | `POST /activities/batches`, `POST /activities/batches/legacy` | [activity-batches.md](../../docs/prd/activity-batches.md), [요구사항](../../docs/requirements/activity-batches.md) |
| 날짜별 관찰 타임라인 | `observation` | `GET /activities/timeline` | [observation-timeline.md](../../docs/prd/observation-timeline.md) |
| 집중 세션 | `observation` | `GET·POST /focus-sessions`, `PUT /focus-sessions/{focus_session_id}/completion` | [focus-sessions.md](../../docs/prd/focus-sessions.md) |
| 라벨 목록 | `labels` | `GET /labels` | [activity-labeling.md](../../docs/prd/activity-labeling.md) |
| 라벨 제안·확정·라벨 타임라인 | `labeling` | `POST /activities/label-confirmations`, `PUT /activities/segments/{segment_id}/label-confirmation`, `GET /activities/segments/{segment_id}/label-state`, `GET /activities/label-timeline` | [activity-labeling.md](../../docs/prd/activity-labeling.md), [activity-label-package-structure.md](../../docs/prd/activity-label-package-structure.md) |

BC 사이 의존과 패키지 구조는 [현재 구조](../architecture/current-structure.md)를, 배치 전달과 타임라인 projection 구현은 [영속성·메시징·배치 실행](../architecture/persistence-and-messaging.md)을 본다.

## 핵심 용어

### 계정과 Device

- **Device**: 한 계정에 귀속되어 서버에 등록된 하나의 앱 설치. 재설치로 등록 상태를 잃으면 새 Device로 취급하며 서버 식별자는 `deviceId`다. "Device Registration"이라고 부르지 않는다([ADR 0003](../../docs/adr/0003-devices-own-device-identity.md)).
- **계정 시간대**: 날짜별 관찰 타임라인의 하루 경계를 정하는 계정의 IANA 시간대.

### 활동 관찰(사실)

- **활동 레코드**: 한 시점의 활동 관찰 또는 수집 가능 상태의 변화를 나타내는 원본 사실.
- **관찰 시각**: Device가 레코드에 기록한 시각. 서버 수신 시각과 구분한다. 같은 Device 안의 순서는 `sequence`로 정한다.
- **관찰 타임라인**: 계정의 모든 Device에서 관찰된 활동과 공백을 시간순으로 배치한 기록. Device 출처나 AI 해석을 포함하지 않는다.
- **관찰 활동 구간**: 동일한 관찰 문맥이 60초를 넘겨 끊기지 않고 이어진 반개구간 `[시작, 종료)`.
- **수집 공백**: 수집 중단이나 관찰 실패로 실제 활동을 알 수 없는 시간. 명시적인 `suspended`로 시작하며 순번 누락이나 침묵만으로 추정하지 않는다.
- **불투명 활동**: 관찰했지만 개인정보 보호를 위해 식별 정보를 남기지 않은 정상 활동. 라벨링 대상에서 제외한다.
- **집중 세션**: 사용자가 직접 남긴 작업 의도 기록. 관찰 레코드가 진행 중인 세션을 가리킬 수 있지만, 세션 라벨은 관찰 구간의 확정 라벨과 별개다.

### 활동 해석

- **라벨**: 사용자가 활동의 의미로 붙이는 사용자별 대표 분류. 한 구간에 최대 하나.
- **라벨 제안**: AI가 사용 중인 라벨 중에서 골라 제시한 확정 전 판단. 이미 만든 제안은 조용히 바뀌지 않는다.
- **확정 라벨**: 사용자가 승인하거나 다른 라벨을 골라 확정한 라벨. 최신 확정값만 집계와 이후 제안에 반영된다([ADR 0005](../../docs/adr/0005-separate-observation-and-personal-label-confirmation.md)).
- **미분류**: 근거가 부족해 라벨이 없는 상태. AI가 남겨 두거나 사용자가 미분류로 확정할 수 있다.
- **라벨별 활동 시간 / 검토 대기 활동 시간 / 불투명 활동 시간**: 확정 시간, 미확정 시간, 불투명 시간을 서로 섞지 않고 따로 집계한다.

## 관련 페이지

- [현재 구조](../architecture/current-structure.md)
- [유효한 결정](../decisions/active-decisions.md)
