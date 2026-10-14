# 마일스톤: 개발용 in-memory 활동 배치 API와 비동기 Handler

## 0. 승인·작업 범위

사용자는 이번 마일스톤의 개발 착수를 명시적으로 승인했다. 이전 Account–Device 리팩토링은 되돌렸으며 재도입하지 않는다.

- Worktree: `/Users/jolvy/workspace/project/mosemo/mosemo-server-worktrees/backend-modules`
- Branch: `refactor/backend-modules`
- 현재 기준 HEAD: `f2737b9` (Account–Device 등록 리팩토링 취소)
- 추적 코드의 원래 기준: `3178eb0556393535145a7135b762f2db03e8e1b3`
- 합의 지식: `/Users/jolvy/workspace/wiki/mosemo/decisions/batch-acceptance-contract.md`
- 모델: `gpt-6.1-sol`; 단계별 reasoning은 PO가 선택하며 high 이하.
- 최신 사용자 지시로 검증·독립 리뷰 완료 후 범위 내 커밋·푸시·PR 생성은 허용한다. 자동 병합은 하지 않는다.
- 실제 Kafka 연결·전체 모듈 이동은 승인 범위 밖이다.

진행 순서: **GOAL 갱신 → 작은 기술 검증 → 배치 API·Handler 구현 → 회귀 검증 → 독립 리뷰**.

## 1. 이번 목표

개발용 API는 배치를 메모리 전달 경로에 수락하면 `202`를 반환하고, 별도 처리 루프에서 Handler가 원본과 타임라인을 DB에 반영한다. 화면은 DB 타임라인 polling으로 결과를 조회한다.

```text
HTTP Batch Router
 → 인증·입력·Device 소유권 검증
 → 메모리 전달 경로에 전체 배치 수락
 → 202 {batchId, status: accepted}

별도 처리 루프
 → FastStream에 명시적으로 등록한 Handler
 → 원본 선택·저장 + 타임라인 갱신
 → DB commit

클라이언트
 → 수락 응답 확인 후 해당 로컬 배치 제거
 → 열린 화면에서 DB 타임라인 GET polling
```

API는 Handler/DB 완료를 기다리지 않는다. 개발 단계의 프로세스 종료 시 미처리 메모리 원본 유실 가능성을 사용자가 명시적으로 허용했다. 현재 accepted는 내구성 있는 Kafka ACK 또는 DB commit 보장이 아니다. 운영 내구성 보장으로 오해시키지 않는다.

## 2. 필수 기술 검증

[faststream_fastapi](https://github.com/faststream-community/faststream_fastapi)와 FastStream을 사용한다. FastStream의 확인된 in-memory 기능은 TestBroker 기반이며 await publish가 Handler 완료를 기다릴 수 있다.

실제 라이브러리/현재 Python·FastAPI 조합으로 scratch spike를 실행해 검증한다.

1. 외부 Kafka 없이 Handler가 실제 실행되고 payload가 올바르게 전달된다.
2. Handler를 의도적으로 대기시킨 동안 HTTP 수락 응답이 먼저 완료된다.
3. Handler가 종료된 HTTP 요청의 DB 세션을 사용하지 않고 메시지별 독립 세션/트랜잭션을 연다.
4. app lifespan으로 Worker/Broker가 시작·종료하고 미관찰 task 예외가 남지 않는다.
5. 전달 실패/용량 초과에서는 수락 성공을 거짓으로 반환하지 않는다.

지원되지 않는 API를 가정하거나 운영용 in-memory Broker를 제공한다고 허위 보고하지 않는다. 필요한 경우 개발용 bounded queue와 Worker 같은 얇은 전달 어댑터를 검토한다. TestBroker의 테스트 구현을 운영용으로 일반화하지 않는다. 간단한 어댑터로 검증되지 않으면 작업을 중단하고 PO에게 실행 결과와 대안을 보고한다.

## 3. API 계약

- `POST /api/v1/activities/batches`, Bearer 인증.
- 기존 단건 POST 등록을 배치 API로 대체한다. 기존 GET timeline 및 라벨/집중/계정 API는 유지한다.
- 최상위 요청: `batchId` UUID, `deviceId` UUID, `records` 배열.
- accountId는 인증 결과에서 결정하며 body에서 받지 않는다.
- 개별 원본에서 deviceId 반복을 제거한다. eventId, sequence, recordType, observedAt, timezoneId, utcOffsetMinutes, 선택 focusSessionId, 종류별 context 또는 state/reason의 기존 의미·검증은 유지한다.
- schema/빈 배열/건수/필드 오류는 422, 비압축 JSON 본문 512 KiB 초과는 413, 인증 401, Device 존재/귀속 404, 전달 수락 실패/과부하는 503. 기존 공통 오류 형식을 사용한다.
- 입력 검증 실패 시 전체를 전달하지 않는다. 메모리 전달은 배치 한 단위다.
- 성공: `202 {"batchId": "...", "status": "accepted"}`. receivedAt/원본별 처리 건수/DB 완료 시각은 반환하지 않는다.
- DB에 이미 저장된 eventId/sequence 대조는 Handler에서 수행한다.
- 같은 batchId는 같은 account/device와 같은 원본 내용으로 재시도한다. 다른 내용은 내부 거절/진단 대상으로 처리한다. 동기 receipt DB 조회 없이 HTTP 즉시 409를 보장하지 않는다.

## 4. Handler 처리 규칙

- 이미 처리 결과가 있는 eventId는 다시 처리/덮어쓰기하지 않는다. 소유권/계정 경계는 여전히 보호한다.
- 같은 Device sequence의 서로 다른 원본은 먼저 온 기록을 유지하고 후발 기록만 거절한다.
- 숫자 sequence 연속성을 요구하지 않는다. 누락/삭제·역순 도착 때문에 기다리거나 무조건 거절하지 않는다.
- 미사용 낮은 sequence도 저장하고 타임라인을 재구성한다.
- 충돌 원본만 제외하고 채택된 원본과 구간 변경을 하나의 DB 트랜잭션으로 반영한다. DB 처리 장애 시 채택 집합의 변경은 전부 rollback한다.
- 한 배치 내부 중복도 한 원본만 반영하고 충돌의 승자 선택이 결정적이어야 한다.
- focusSessionId의 소유권/시간/수집상태 규칙은 보존한다. 유효하지 않은 연관 원본은 범위가 불명확하면 PO에게 구체적 정책을 확인한다.
- in-memory Worker/실제 재시작에서 원본 중복은 안전해야 한다. 기존 UNIQUE/FK와 필요한 DB 동시성 보호를 없애지 않는다.

## 5. 타임라인 계산 목표

관찰 순서는 동일 Device의 sequence를 기준으로 하며 관찰 시각은 구간 경계 계산의 근거로 보존한다. Device별 sequence를 계정 평생 전역 순번으로 취급하지 않는다. 교체·여러 Device 이력은 현재 계정 projection의 의미를 보존하는 최소 연결 방식이 필요하며 새로운 교체 정책을 구현하지 않는다.

- 뒤에 이어지는 입력은 마지막 상태를 증분 갱신하는 방향.
- 과거 삽입은 영향받는 지점부터 재구성.
- 같은 배치의 과거 변경은 가능한 한 묶어서 계산.
- 기존 상태/경계와 다시 일치하는 안전한 경계에서 이후 처리를 중단.
- 원본 전체를 매번 조회하거나 무조건 전부 delete/reinsert하는 것을 정상 경로로 만들지 않는다.
- 참고용 full replay와 증분/부분 결과의 관찰 의미를 비교해 정확성을 검증한다. 구현 비용 때문에 정확성을 희생하지 않는다.
- 전체 context 동등성, opaque, 명시적 suspended/active 공백, 60초 병합 의미, 0초 구간, 날짜를 넘는 구간, 내용 버전과 라벨 적용성을 보존한다.
- 시간 역행을 음수 간격의 정상 병합으로 처리하지 않는다. 단조 시계 도입은 범위 밖이며 거짓 경계/자동 원본 시간 보정도 하지 않는다. 시간 이상 처리에서 제품 판단이 필요하면 구체적 입력/결과와 질문을 PO에 제시한다.

## 6. 구조·OOP·순수 로직

업무 이벤트와 Handler는 activities에, 공통 전달·lifespan 기반은 infra에 둔다. 기존 전체 디렉터리와 다른 모듈을 한꺼번에 옮기지 않는다.

- 객체는 실제 처리 상태·업무 행위를 소유한다. 이름만 위임하는 Aggregate/서비스를 늘리지 않는다.
- 원본 선택/grouping/version 등 계산은 외부 I/O 없는 함수로 분리한다. 입력은 명시적이고 몰래 변하지 않는다.
- Handler는 메시지 경계와 작업 서비스/객체를 연결한다. 순수 로직과 업무 객체는 FastStream/FastAPI에 의존하지 않는다.
- DB 세션 생성·트랜잭션·UUID·시각·전송·재시도는 imperative shell에서 조율한다.
- Handler 등록은 명시적이고 주입 가능한 app 구성 지점에서 수행한다. import만으로 DB/Worker/네트워크를 실행하지 않는다.
- FastStream을 단순 import만 하고 Handler를 별도 수동 함수로 직접 호출해 라이브러리 사용을 가장하지 않는다.

## 7. 개발용 전달·실패 처리

- 무제한 fire-and-forget task를 만들지 않는다. bounded 전달량과 고정 Worker를 app lifespan에서 관리한다.
- 같은 계정/Device의 타임라인 갱신 경쟁은 기존 DB 보호를 보존한다. in-memory에는 Kafka partition 직렬화 보장이 없다고 가정한다.
- Handler 일시 장애는 최초 포함 총 5회, 대기 상한 1/2/4/8초와 jitter로 재시도한다.
- 정상적인 중복/sequence 충돌은 재시도 대상 장애가 아니다.
- 개발용 실패·보류 원본은 제한된 메모리 보관/진단 경로로 관리할 수 있다. Kafka 격리 토픽은 후속 도입이며 현재 실제 토픽을 만들지 않는다.
- 메모리 수락 불가이면 503. 무제한 backlog를 숨기지 않는다.
- DB에 저장하지 못한 원본이 프로세스 종료로 사라질 수 있음을 개발용 문서/설정으로 명확히 한다.
- 실패 Device 보류, 다른 계정 처리 지속, 복구 조회/재처리의 최소 개발용 경로를 검토한다. 임의 원본 수정/삭제/offset 흉내로 성공을 만들지 않는다.
- 정상 계산 hard timeout은 근거 없이 짧게 고정하지 않는다.

## 8. 클라이언트 연동 범위

사용자가 승인한 전체 흐름에는 열린 화면 자동 갱신이 포함된다. 형제 macOS 저장소는 기존 작업을 보존하면서 **배치 생성·전송·수락 확인·타임라인 polling에 필요한 부분만** 수정 가능 여부를 PO가 확인하고 분리된 작업 범위로 지시한다. 서버 worktree 밖을 Codex가 임의로 수정하지 않는다.

- 초기 배치 대기 10초 / 최대 200건 / 비압축 본문 512 KiB. 전송 가능 상태에서 먼저 도달한 조건으로 전송.
- Device별 단일 in-flight, 적체는 ACK 후 즉시 다음 배치.
- 응답 미확인은 같은 batchId/내용 재시도, 로컬 원본 보존.
- backoff 상한 1/2/4/8/16/32/60초와 full jitter, Retry-After 존중.
- 발행 전 명확한 413은 원본 ID/sequence/본문을 유지한 재분할과 새 batchId를 허용. 원본 단건 초과는 절삭하지 않는다.
- 화면 열기/재활성화 즉시 조회, 열린 화면 초기 5초 polling, 숨김 시 중단, 요청 겹침/오래된 응답 방지, 오류 시 기존 화면 유지와 backoff.
- 다른 macOS 기능·UI 전체 재설계·인증 정책 변경은 제외.

## 9. 이번에 하지 않을 것

- 실제 Kafka 연결·배포·retention/용량/복제 설정·Outbox·CDC.
- Spark/분석/AI 신규 기능.
- 전체 모듈 이동과 Account–Device 리팩토링 재도입.
- 단조 시계와 단일 활성 Device 강제 기능.
- 기존 적용 migration 다시 쓰기, 의미 없는 general event framework, 측정하지 않은 성능 개선 주장.
- 범위 밖 저장소/프로필 설정 수정, 비밀 복사, 사용자 DB 사용.

## 10. 종료 조건·검증

단계별 TDD(한 행동 RED→GREEN→정리), 실제 FastStream spike, HTTP 응답 선반환/실제 비동기 DB 반영, 입력 거절, 중복/충돌/누락/역순, commit/rollback, lifecycle/고립 세션/Worker 오류, 라벨·focus·날짜 경계 회귀를 검증한다.

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked ty check
uv build
uv run --locked poe test
uv run --locked poe openapi
git diff --check
```

OpenAPI의 의도된 새 계약 변화만 반영한다. dependency 변경은 FastStream 경로에 필요한 최소 범위로 제한한다. 단계별 진척을 보고하며, blocker·새 제품 판단이 필요하면 근거를 제시하고 다음 단계로 무단 진행하지 않는다. 최종 보고는 실행한 검증, 미실행 검증, 선택/기각 이유, 개발용 한계를 구분한다.
