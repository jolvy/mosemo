# Mosemo 서버 아키텍처

## 주요 영역과 패키지

```text
src/mosemo/
├── accounts/          # 계정
├── auth/              # 로그인·인증
├── devices/           # 계정에 등록된 앱 설치
├── activities/        # 활동 관찰 원본과 관찰 타임라인
├── activity_labels/   # 관찰에 대한 개인 라벨 해석
│   ├── catalog/       # 라벨 목록
│   ├── confirmations/ # 사용자 확정·정정
│   ├── proposals/     # AI 제안과 작업자
│   └── queries/       # 여러 모듈을 결합하는 조회
└── focus_sessions/    # 사용자가 의도한 집중 작업 기록
```
