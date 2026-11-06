# 테스트

이 문서가 테스트 분류와 위치의 원본이다. `tests/` 구조 규칙은 [tests/unit/harness/test_tests_layout.py](unit/harness/test_tests_layout.py)가 강제한다.

## 테스트 분류 규칙

| 분류 | 위치 | 기준 |
| --- | --- | --- |
| Unit | `tests/unit/{패키지}/` | 하나의 논리적 단위(SUT)의 동작을 검증한다. SUT 밖의 복잡한 의존성은 Fake·Mock·Stub으로 격리할 수 있다. 다른 실제 구성요소의 협력이나 업무 흐름 전체를 검증하는 것이 주목적이 아니다. |
| Integration | `tests/integration/{패키지}/` | 실제 구성요소 사이 연동을 검증하거나, 컴포넌트 하나를 공개 인터페이스(HTTP·메시지·명령어·신호)로 호출해 내부 구성요소의 협력을 검증한다. Component 테스트, Repository 테스트, 프로세스 수명주기 테스트를 포함한다. 외부 시스템은 목적에 따라 Fake·Mock으로 바꿀 수 있다. |
| E2E | `tests/e2e/`(첫 테스트를 쓸 때 만든다) | PRD 「사용자와 사용자 시나리오」의 핵심 업무 시나리오를 외부 진입점부터 최종 결과까지 검증한다. 테스트가 띄운 서버 프로세스에 실제 HTTP로 요청한다(`ASGITransport`로 앱을 프로세스 안에서 부르면 E2E가 아니다). 일부 외부 서비스(Kakao, AI 모델)는 통제 가능한 대역으로 바꿀 수 있지만 핵심 경로는 실제 구현을 쓴다. 단순 API 호출이나 DB 저장만으로 E2E로 분류하지 않는다. |

- 분류 우선순위: 정의된 핵심 업무 시나리오 전체를 검증하면 E2E → 아니고 실제 구성요소 연동이나 컴포넌트의 공개 동작을 검증하면 Integration → 아니고 격리된 단위의 동작을 검증하면 Unit. 어디에도 명확히 해당하지 않으면 임의로 정하지 않고 SUT와 검증 목적을 확인한다.
- 테스트 파일 하나는 한 분류에만 속한다. 여러 분류에 걸치면 주된 검증 목적과 위 우선순위로 정한다.
- E2E는 핵심 업무 시나리오에 한정하고, Unit·Integration으로 충분히 검증할 수 있는 사례로 넓히지 않는다.
- `{패키지}`는 SUT가 있는 `src/mosemo`의 최상위 패키지(`identity`, `labels`, `observation`, `labeling`, `shared`, `platform`, `entrypoints`)다. 예외: 저장소 도구 테스트 `harness`(`.codex`), `scripts`, 마이그레이션 테스트 `migrations`, 테스트 지원 코드 자체를 검증하는 `support`(`tests/support`). 그 아래 깊이는 자유다.
- 마커는 "어떤 조건에서 실행하나"만 나타낸다: `requires_db`(실제 PostgreSQL), `requires_kafka`(실제 Kafka). `tests/conftest.py`가 fixture를 보고 자동으로 붙이며 손으로 붙이지 않는다. 새 컨테이너 fixture를 추가하면 마커 매핑도 함께 추가한다.
- 경계 판정 기록: `entrypoints/test_providers.py`, `test_exception_handlers.py`는 provider·handler 하나만 등록한 작은 FastAPI 앱에 `TestClient`를 쓰지만 SUT가 그 함수 하나이므로 Unit이다. `platform/test_database.py`(설정 없는 새 인터프리터에서 import 확인)와 `harness/test_docs.py`(검사기 CLI)의 subprocess도 같은 이유로 Unit이다.
