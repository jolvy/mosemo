# Mosemo Domain

Mosemo가 계정에 귀속된 앱 설치와 그 설치에서 관찰한 활동을 일관된 용어로
설명하기 위한 도메인 언어다.

## Language

**Device**:
한 계정에 귀속되어 서버에 등록된 하나의 앱 설치다. 앱 재설치 등으로 등록 상태를
잃으면 이전 Device와 자동으로 연결하지 않고 새 Device로 취급하며, 서버 식별자는
`deviceId`다.
_Avoid_: Device Registration, physical device, hardware identity
