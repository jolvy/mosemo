# Mosemo 서버 아키텍처

패키지 구조와 의존 규칙은 [ADR 0011](docs/adr/0011-split-bounded-contexts-logically-with-layered-packages.md)과
[ADR 0015](docs/adr/0015-group-entrypoints-and-platform-packages.md)를 따르고,
`pyproject.toml`의 import-linter 계약(`poe lint-imports`)이 강제한다.

## 주요 영역과 패키지

```text
.
├── openapi/
│   └── openapi.json        # 공개 API 스냅샷
└── src/mosemo/
    ├── identity/          # BC: 계정, 로그인·토큰, 계정에 등록된 앱 설치(Device)
    ├── labels/            # BC: 계정의 라벨 목록, 가입 시 기본 라벨
    ├── observation/       # BC: 활동 레코드, 관찰 타임라인 투영과 조회, 집중 세션
    ├── labeling/          # BC: 라벨 확정·정정, AI 라벨 제안과 제안 job, 라벨 타임라인
    ├── shared/            # 공용 커널(domain·application·presentation)
    ├── platform/          # 누구 소유도 아닌 실행 기반: 설정·로깅·시계·provider·스케줄링
    │   ├── database/      # DB 엔진·세션(engine), 매핑 registry·MetaData(persistence), 트랜잭션 헬퍼(transactions)
    │   └── messaging/     # 업무 메시지를 모르는 Kafka 발행·구독·런타임 어댑터, Kafka 호환(kafka_compat)
    └── entrypoints/       # 조립 루트(프로세스마다 하나)
        ├── api/           # FastAPI 앱, 미들웨어, 예외 handler
        ├── consumer/      # 활동 Consumer 진입점
        └── batch/         # 배치 job 등록과 진입점
```

- Bounded Context 사이 의존은 labeling → observation → labels → identity 방향으로만 둔다.
- BC마다 루트에 `public.py`(다른 BC용)·`bootstrap.py`(조립 루트용)·`__init__.py`를 두고, 계층은
  presentation → application → infrastructure → domain 방향으로만 의존한다.
- BC는 `mosemo.entrypoints`를 직접·간접 모두 import하지 않는다. `shared`·`platform`은 BC와 진입점을
  import하지 않는다.
- 테이블 메타데이터는 각 BC `bootstrap.py`의 매핑 함수가 `platform.database.persistence`의 `MetaData`에 등록하며,
  Alembic 환경(`migrations/env.py`)도 이 함수만 호출한다.
