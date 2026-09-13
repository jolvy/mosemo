# TODO

## 인증

- [ ] 새 기기 로그인 시 기존 access token을 즉시 무효화할 수 있는 서버 관리 활성
  세션을 도입한다.
  - 현재 JWT의 `exp` 기반 자연 만료는 유지한다.
  - access token을 `device_registration_id`와 활성 세션에 연결한다.
  - 새 로그인이 활성 세션을 교체하면 이전 세션의 JWT는 남은 만료 시간과 관계없이
    거절한다.
  - 필요한 `accounts` 변경은 활동 저장 모델 구현과 분리한다.
