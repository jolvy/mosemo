---
status: accepted
date: 2026-09-15
---

# Device 식별자는 devices 경계가 소유한다

Mosemo에서 `Device`는 물리 하드웨어가 아니라 계정에 귀속되어 서버에 등록된 하나의
앱 설치다. `devices`가 Device 식별자, 생성 멱등성, 계정 소유권 조회를 소유하고,
`accounts`는 계정만 소유하며 `activities`는 `deviceId`로 Device를 참조한다. 앱
재설치 등으로 등록 상태를 잃으면 이전 Device와 자동으로 연결하지 않고 새 Device로
취급한다.

## 결정

- 공개 API, 애플리케이션 모델, 데이터베이스에서 `Device`와 `deviceId`를 공통
  용어로 사용한다. `DeviceRegistration`과 `deviceRegistrationId`는 사용하지 않는다.
- Device 등록 API는 `POST /api/v1/devices`이며, 현재 `devices`의 기능 범위는 인증된
  계정에 Device를 등록하고 서버 식별자를 발급하는 것뿐이다.
- `DeviceRepository`는 Device 생성, 등록 요청의 멱등 조회, 계정 소유권 조회를
  담당한다. 활동 저장은 이 저장소를 통해 소유권을 확인하고 Device 저장 구조를
  직접 조회하지 않는다.
- 아직 운영 중인 스키마가 아니므로 호환용 rename migration을 만들지 않는다. 초기
  활동 저장 migration이 처음부터 `devices`, `device_id`, `idempotency_key`를
  생성하도록 수정한다.

## 고려한 대안

- `accounts`가 Device를 소유하면 계정과 앱 설치의 수명주기 및 저장 책임이 섞인다.
- `activities`가 Device를 소유하면 인증 및 다른 Device 기능이 활동 수집에 종속된다.
- 패키지만 `devices`로 부르고 저장 모델을 `DeviceRegistration`으로 유지하면 계층마다
  용어 변환과 두 개의 공개 식별자 이름이 남는다.

## 결과

- 의존 방향은 `accounts ← devices ← activities`가 된다.
- 기존 `deviceRegistrationId` 공개 필드는 호환 alias 없이 `deviceId`로 교체된다.
- macOS 클라이언트 변경은 이 결정의 서버 구현 범위에 포함하지 않는다.
