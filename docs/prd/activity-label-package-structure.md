# 활동 라벨 모듈의 패키지 재배치

## Problem Statement

백엔드 개발자는 활동 라벨 API를 수정하려고 할 때 일반적인 라벨 목록 관리와
관찰 구간의 확정·정정, AI 제안 처리, 사용자용 조회가 섞인 패키지부터 탐색해야 한다.
현재 라벨 라우터가 활동 API를 제공한다는 사실도 패키지 이름만으로는 드러나지 않는다.
어떤 변경을 어느 모듈에서 해야 하는지 판단하기 어렵고, 확정 응답을 조합하는 과정에서
확정 처리와 제안 조회의 책임이 연결되어 있다.

이 구조를 정리하면서도 이미 구현된 버전 검증, 배치 원자성, 재시도, 계정별 동시성 제어를
유지해야 한다. 패키지를 나누는 이유와 바운디드 컨텍스트를 나누는 이유가 혼동되면
현재 프로젝트에 필요하지 않은 운영 구조까지 추가될 수 있다.

## Solution

현재 탐색한 관찰·라벨 목록·사용자 확정·AI 제안 흐름을 하나의 바운디드 컨텍스트로
취급하고, 그 안에서 책임에 따라 패키지를 재배치한다. 관찰 사실과 현재 관찰 구간은
`activities`에 두고, 활동 라벨 관련 기능은 `activity_labels`에 모은다.

활동 라벨 내부는 `catalog`, `confirmations`, `proposals`, `queries`로 나눈다.
상위 애플리케이션 서비스가 확정 요청의 트랜잭션과 계정별 잠금을 관리하고,
확정 검증·저장과 응답 조합을 같은 세션에서 조율한다. 공개 API와 저장 결과는 유지한다.

## User Stories

1. As a backend developer, I want activity-label APIs to live in a clearly named module, so that I can find the implementation from the business responsibility.
2. As a backend developer, I want observation facts and derived observation segments to remain in the observation module, so that their ownership is clear.
3. As a backend developer, I want the account-owned label catalog to have its own module, so that catalog changes have an identifiable location.
4. As a backend developer, I want confirmation and correction behavior to belong to the confirmations module, so that I can change user-selection rules together.
5. As a backend developer, I want proposal processing and its retry lifecycle to belong to the proposals module, so that AI processing has a coherent implementation boundary.
6. As a backend developer, I want composed user-facing reads to belong to the queries module, so that confirmation writes do not need to construct proposal-aware views.
7. As a backend developer, I want single-module reads to remain with their owning module, so that a shared query module does not become the location for every read.
8. As a backend developer, I want proposal-specific confirmed-example retrieval to remain inside proposals, so that suggestion input construction stays with its consumer.
9. As a backend developer, I want one observation-owned segment-version calculation, so that confirmation, proposal, and query code use the same unchanged version semantics.
10. As an API consumer, I want the existing routes, operation IDs, JSON fields, public schemas, tags, and errors to remain unchanged, so that package relocation does not require client changes.
11. As a user, I want to confirm a label or 미분류 without a successful AI proposal, so that I can review my activities when the model is unavailable.
12. As a user, I want my own choice to take precedence over a proposal, so that an AI result does not replace my confirmed interpretation.
13. As a user, I want each item in a batch to retain its own selection, so that submitting multiple activities together preserves my individual choices.
14. As a user, I want a failed batch item to roll back the whole batch, so that I do not receive a partially applied review.
15. As a user, I want an identical successful batch retry to preserve confirmation timestamps, so that retrying after response loss does not create a new confirmation.
16. As a user, I want a later correction to remain authoritative when an old batch is retried, so that a delayed request does not undo my correction.
17. As a user, I want stale segment versions to be rejected using the existing error contract, so that a label is not silently attached to changed observation facts.
18. As a user, I want unchanged segments to retain valid label state after projection recalculation, so that unrelated observations do not invalidate my work.
19. As a user, I want label-state reads and label-timeline reads to retain their existing results, so that module separation does not change the review experience.
20. As a user, I want the label timeline to retain its current grouping, time-zone handling, and boundaries, so that gaps and different confirmation states are still visible.
21. As a user, I want my catalog to include my active and archived labels as it does today, so that I can recognize labels referenced by past confirmations.
22. As a user, I want archived labels to stay out of new selections and proposal candidates, so that an archived classification is not used for new activity.
23. As a user, I want account ownership checks to remain intact across all moved modules, so that another account cannot read or change my label state.
24. As a new user, I want the existing default labels to be prepared with account creation in one transaction, so that I receive a consistent initial catalog.
25. As a user, I want slow suggestion work to allow confirmation and observation collection to continue, so that model latency does not hold the account timeline lock.
26. As a user, I want proposal results to be revalidated after a concurrent confirmation or observation change, so that an outdated result does not become current.
27. As an operator, I want the existing label-worker command and lease/retry behavior to keep working, so that package relocation does not require a new operating procedure.
28. As a maintainer, I want existing database mappings and migrations to keep working, so that reorganizing code preserves stored data and schema compatibility.

## Implementation Decisions

- **컨텍스트의 범위**: 현재 합의한 관찰·라벨 목록·확정·제안은 하나의 바운디드 컨텍스트 안의 모듈로 둔다. 인증과 Device의 바운디드 컨텍스트 판단은 이번 작업에 포함하지 않는다.
- **패키지 소유권**: 기존 라벨 패키지의 책임을 `activity_labels`로 재배치한다. `catalog`는 라벨 모델·저장소·목록 API·기본 라벨 준비를, `confirmations`는 확정 모델·저장소·검증·저장을, `proposals`는 제안 모델·저장소·스캔·처리기·사례 검색·제안기·작업자를 소유한다. `queries`는 여러 모듈을 결합하는 조회와 응답 조합을 소유한다. 공유 공개 요청·응답 스키마는 활동 라벨 상위 모듈에 둔다.
- **현재 동작 유지**: 기존 모델이 소유한 판정과 상태 전이는 유지한다. 이번 작업의 목적에 필요한 책임 분리와 의존성 변경만 수행하며, 모든 도메인 규칙을 새 객체로 재설계하지 않는다. 라벨은 활동 해석에 쓰이는 계정별 목록으로 다룬다.
- **사용자용 조회의 범위**: 구간 라벨 상태는 현재 관찰 구간과 내용 버전, 현재 버전에 적용 가능한 확정, 제안을 조합한다. 날짜별 라벨 타임라인은 관찰 구간, 적용 가능한 확정, 현재 라벨 이름을 조합한다. 현재 라벨 타임라인은 제안을 조회하지 않으며, 이번 작업에서 제안 표시를 추가하지 않는다. 목록 조회는 `catalog`에, 제안 입력용 확정 사례 검색은 `proposals`에 둔다.
- **의존 방향**: 활동 라벨 라우터는 상위 애플리케이션 서비스를 호출하고 목록 라우터는 목록 모듈의 조회를 호출한다. 상위 서비스는 확정 처리와 사용자용 조회를 조율한다. 확정 모듈은 관찰과 목록에 의존하며 제안이나 사용자용 조회에 의존하지 않는다. 제안 모듈은 관찰·목록·확정 이력을 읽는다. 사용자용 조회는 응답에 필요한 관찰·목록·확정·제안을 읽는다. 하위 모듈은 상위 서비스를 참조하지 않고, 관찰 모듈은 활동 라벨 모듈을 참조하지 않는다.
- **구간 버전**: 구간 내용 버전 계산은 관찰 모듈로 옮긴다. 해시 입력, 정규화, 계산 결과와 호출 시점의 의미를 유지한다. projection 식별자를 영구 도메인 식별자로 바꾸거나 새로운 버전 컬럼을 도입하지 않는다. 늦은 관찰로 내용이 바뀐 구간의 기존 제안·확정을 새 내용에 조용히 이전하지 않는다.
- **확정 요청의 조율**: 상위 애플리케이션 서비스가 요청의 트랜잭션, 계정별 타임라인 잠금, 작업 순서를 소유한다. 확정 모듈의 검증·저장과 조회 모듈의 응답 조합은 같은 세션·트랜잭션에서 실행한다. 하위 모듈은 이 요청 안에서 별도 트랜잭션을 시작하거나 커밋하지 않는다. 응답 조합을 위해 제안 상태가 필요하면 조회 모듈이 읽는다.
- **배치와 정정**: 모든 배치 항목을 검증한 뒤 저장하고, 어느 항목이든 실패하면 전체 요청을 롤백한다. 중복 대상 거절, 항목별 선택, 버전 충돌, 소유권·적격성 검증과 공개 오류를 유지한다. 성공한 동일 선택의 재시도는 기존 시각을 유지하고, 그 사이 단일 구간 정정이 있었다면 이전 배치가 정정을 덮어쓰지 않는다. 라벨이 이후 보관되었더라도 이미 적용된 동일 확정의 재시도에 대한 현재 동작을 유지한다.
- **제안 처리의 조율**: 제안 작업자는 선점과 결과 반영에 각각 짧은 트랜잭션을 사용한다. 확정 사례 검색과 AI 호출은 선점 트랜잭션을 마친 후 진행하고, 느린 외부 호출 동안 계정 타임라인 잠금을 보유하지 않는다. 최종 반영에서는 임대, 현재 구간 버전, 사용자 확정, 활성 라벨 후보를 다시 검증한다. 현재 실패·재시도·대체 상태와 시도 제한을 유지한다. 이 흐름은 제안 모듈의 애플리케이션 처리기가 조율하며 HTTP 확정 서비스에 합치지 않는다.
- **목록과 인증의 연결**: 기본 라벨 종류와 중복 없는 준비는 목록 모듈이 소유한다. 인증 흐름은 새 계정 생성 시 이를 호출하고 계정 생성과 같은 트랜잭션으로 묶는다. 계정별 활성·보관 목록 조회, 새 후보의 활성 라벨 제한, 과거 확정의 라벨 참조와 현재 이름 표시를 유지한다. 공개 목록 쓰기 기능은 추가하지 않는다.
- **공개 계약**: 기존 활동 등록·관찰 타임라인, 라벨 상태·타임라인·단일 확정·배치 확정 및 라벨 목록의 경로, HTTP 메서드, operation ID, JSON 필드, 공개 스키마 이름, API 설명, 태그, 상태 코드, 오류 코드와 헤더를 유지한다. 서버의 내부 Python import 경로는 재배치에 맞춰 모두 갱신한다.
- **영속화와 실행 경로**: SQLAlchemy Declarative Mapping, 테이블·컬럼·제약 조건·저장 형식을 유지한다. 재배치한 모델의 metadata 등록, API 등록, 의존성 주입, 인증 호출, 작업자 실행 경로와 테스트 import를 함께 갱신한다. 이미 적용된 마이그레이션은 수정하지 않는다. 운영자가 사용하는 `poe label-worker` 명령을 유지한다.

## Testing Decisions

- **주요 검증 경계**: 기존 인증 HTTP 요청부터 실제 PostgreSQL까지의 통합 테스트를 중심으로 검증한다. 패키지·클래스 이름이나 저장소 호출 순서를 검증하는 테스트로 공개 동작 검증을 대체하지 않는다. 테스트를 옮기기 위해 새 HTTP API나 테스트 전용 제품 인터페이스를 만들지 않는다.
- **제안 모듈의 검증 경계**: `proposals`가 맡는 제안 스캔·처리는 기존 Processor와 Scanner 진입점을 사용한다. 현재 이 처리는 HTTP 요청과 별도로 실행된다. AI 서비스는 기존 고정 응답·실패·대기 가능한 제안기 대역으로 제어하고, 현재와 같은 실제 PostgreSQL 및 공개 HTTP 조회에서 결과를 확인한다. 모델의 확률적인 응답이나 실제 외부 서비스 가용성을 성공 조건으로 삼지 않는다.
- **확정과 정정**: 제안 없이 직접 확정, 제안과 다른 선택, 미분류 확정, 단일 구간 정정, 항목별 배치 선택, 동일 재시도의 시각 보존, 정정 이후 지연된 배치 재시도 충돌을 검증한다. 잘못된 항목이 배치의 처음이나 뒤에 있어도 성공한 일부 항목이 남지 않아야 한다.
- **버전과 동시성**: 오래된 버전, 늦은 관찰에 따른 재구성, 영향받지 않은 구간의 버전 유지, 같은 결과의 재계산, 제안 처리 중 사용자 확정과 구간 변경, 만료된 임대의 결과 반영을 검증한다. 느린 사례 검색·모델 호출 동안 확정과 관찰 수집이 진행되는 기존 검증을 유지한다. 잠금 대기 실패의 기존 오류와 재시도 헤더도 확인한다.
- **조회와 격리**: 구간 라벨 상태, 제안을 읽지 않는 현재 라벨 타임라인, 연속 그룹, 다른 라벨·확정 상태·공백·0초 구간의 경계, 계정 시간대와 열린 구간을 검증한다. 다른 계정, 미인증 요청, 보관 라벨의 새 선택, 불투명 활동 등 기존 부적격 대상에 대한 결과를 유지한다. 목록은 소유 계정의 활성·보관 라벨만 반환해야 한다.
- **선례**: 기존 활동 라벨 확정 API 통합 테스트의 직접 확정·배치 롤백·재시도·worker 경쟁 조건·타임라인 시나리오와 라벨 목록 API 통합 테스트를 재사용한다. 관찰 타임라인 통합 테스트를 구간 결과 보존의 선례로 삼는다. 빠진 공개 행동 시나리오가 확인되면 해당 경계에 필요한 테스트만 보강한다.
- **구조 변경에 따른 실행 검증**: 기존 마이그레이션 왕복 및 라벨 기본 데이터 검증으로 모델 등록과 기존 저장 구조가 동작하는지 확인한다. API 애플리케이션 import와 기존 작업자 명령 진입이 성공해야 한다. OpenAPI를 다시 내보내 기존 스냅샷과 동일한지 확인하고 런타임 스키마와 스냅샷의 일치 검증을 유지한다.
- **완료 시 검증**: 구현 후 관련 테스트, Ruff 검사와 포맷 확인, ty, 빌드, 전체 테스트, diff 검사를 실행하고 실제 결과를 보고한다. PostgreSQL 통합 테스트에 필요한 Docker가 실행되지 않거나 다른 검증을 실행하지 못했다면 통과로 표현하지 않는다.

## Out of Scope

- 별도 바운디드 컨텍스트·서버·DB로의 분리, 이벤트 메시지 발행, 이벤트 저장소, 새로운 작업 큐 도입.
- 인증과 Device의 전체 책임 및 바운디드 컨텍스트 재설계.
- 서버 전체 도메인 상태 규칙을 객체 협력으로 재구성하는 광범위한 리팩터링.
- 공개 라벨 생성·이름 변경·보관·복원 API, 새 개인화 검색 방식, 라벨별 활동 시간 집계.
- 활동 이외의 대상에 라벨을 붙이는 범용 태그 시스템.
- 공개 API·OpenAPI 변경, 신규 DB 스키마나 마이그레이션, 기존 마이그레이션 재작성.
- 라벨 타임라인의 새 제안 표시, AI 자동 확정, 관찰 구간 내부의 라벨별 시간 분할.
- macOS 클라이언트 변경과 배포 방식 변경.

## Further Notes

- 구현 티켓은 [#46 — 라벨 목록과 기본 라벨 준비](https://github.com/jolvy/mosemo/issues/46), [#47 — 라벨 제안 생성과 작업자](https://github.com/jolvy/mosemo/issues/47), [#48 — 확정·조회와 전체 전환](https://github.com/jolvy/mosemo/issues/48)이다. #47은 #46 완료 후, #48은 #47 완료 후 진행한다.
- 이 명세는 이벤트스토밍에서 합의한 책임 구분을 실제 패키지 구조에 적용하는 작업이다. 바운디드 컨텍스트 결정은 현재 탐색한 흐름의 범위에 한정한다.
- 하나의 바운디드 컨텍스트 안에서도 API와 제안 작업자는 별도 실행 진입점을 가진다. 현재 저장소와 기존 작업자 명령을 유지하며, 큐 도입이나 consumer의 별도 릴리스·배포 설계는 후속 논의로 남긴다.
- 기존 ADR 「활동 원본은 유지하고 관찰 타임라인은 쓰기 시 projection한다」와 「관찰 사실과 개인 라벨 확정을 분리한다」를 유지한다. 이벤트스토밍의 사건 이름을 실제 발행·저장되는 이벤트로 해석하지 않는다.
- [#41](https://github.com/jolvy/mosemo/issues/41)은 서버 전체 도메인 객체의 규칙 소유권에 관한 별도 작업이다. 이번 재배치의 완료 조건에 그 전체 작업을 포함하지 않는다. 두 작업을 같은 영역에서 동시에 진행한다면 변경 순서를 조율한다.
- [#34](https://github.com/jolvy/mosemo/issues/34)의 목록 쓰기와 복원, [#35](https://github.com/jolvy/mosemo/issues/35)의 개인화 확장, [#30](https://github.com/jolvy/mosemo/issues/30)의 일별 집계는 별도 기능이다. 이번 작업에서는 해당 기능의 신규 구현을 요구하지 않는다.
- 기존 통합 테스트를 재배치 전후의 회귀 기준으로 사용한다. 구현자는 현재 체크아웃의 동작을 먼저 확인하고 공개 동작을 보존한 결과와 실제 검증 상태를 보고한다.
