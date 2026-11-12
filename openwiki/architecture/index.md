# 파일

- [현재 구조](current-structure.md) - Mosemo 서버의 Bounded Context(identity·labels·observation·labeling), 공용 패키지(shared·platform), 조립 루트(entrypoints)의 책임과 import 경계를 정리한다.
- [영속성·메시징·배치 실행](persistence-and-messaging.md) - dataclass aggregate와 imperative mapping, 트랜잭션 헬퍼와 도메인 이벤트, Alembic 마이그레이션, Kafka 활동 배치 전달과 Consumer, APScheduler 배치 job의 현재 동작을 정리한다.
