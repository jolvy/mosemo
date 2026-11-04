# Mosemo 서버 아키텍처

> 구조 재편이 진행 중이다. Bounded Context로 옮긴 패키지는 [ADR 0011](docs/adr/0011-split-bounded-contexts-logically-with-layered-packages.md)을 따른다.

## 주요 영역과 패키지

```text
.
├── openapi/
│   └── openapi.json        # 공개 API 스냅샷
└── src/mosemo/
    ├── identity/          # BC: 계정, 로그인·토큰, 계정에 등록된 앱 설치(Device)
    ├── labels/            # BC: 계정의 라벨 목록, 가입 시 기본 라벨
    ├── activities/        # 활동 관찰 원본과 관찰 타임라인
    ├── activity_labels/   # 관찰에 대한 개인 라벨 해석
    │   ├── confirmations/ # 사용자 확정·정정
    │   ├── proposals/     # AI 제안과 제안 배치 작업
    │   └── queries/       # 여러 모듈을 결합하는 조회
    ├── focus_sessions/    # 사용자가 의도한 집중 작업 기록
    ├── shared/            # 공용 커널(domain·application·presentation)
    ├── infra/             # 설정·로깅·DB·영속성·시계·스케줄링·메시징
    ├── api/               # 조립 루트: FastAPI 앱, 미들웨어, 예외 handler
    ├── consumer/          # 조립 루트: 활동 Consumer 진입점
    └── batch/             # 조립 루트: 배치 job 등록과 진입점
```
