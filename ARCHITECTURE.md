# Mosemo 서버 아키텍처

패키지 구조와 의존 규칙은 [ADR 0011](docs/adr/0011-split-bounded-contexts-logically-with-layered-packages.md)을 따르고,
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
    ├── infra/             # 설정·로깅·DB·영속성·시계·스케줄링·메시징
    ├── api/               # 조립 루트: FastAPI 앱, 미들웨어, 예외 handler
    ├── consumer/          # 조립 루트: 활동 Consumer 진입점
    └── batch/             # 조립 루트: 배치 job 등록과 진입점
```

- Bounded Context 사이 의존은 labeling → observation → labels → identity 방향으로만 둔다.
- BC마다 루트에 `public.py`(다른 BC용)·`bootstrap.py`(조립 루트용)·`__init__.py`를 두고, 계층은
  presentation → application → infrastructure → domain 방향으로만 의존한다.
- 테이블 메타데이터는 각 BC `bootstrap.py`의 매핑 함수가 `infra.persistence`의 `MetaData`에 등록하며,
  Alembic 환경(`migrations/env.py`)도 이 함수만 호출한다.
