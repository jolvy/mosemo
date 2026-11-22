---
type: operations
title: 부하 측정 도구
description: infra/measurement의 랩(EKS) 부하 측정 도구를 정리한다. 1천 DAU 피크 고정 부하(137.6 rps) 측정 A, 분석 API·일간 통계 batch를 켠 측정 B, 7B 계정 삭제 운영 규모 측정의 흐름과 고정 기준, 로컬 드라이런, 측정 이력을 안내한다.
tags: [operations, load-testing, k6, measurement, eks, analytics]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-22T12:16:52.428Z
sources:
  - id: openwiki-source-396b24e718ed8ac9a1634261
    resource: repo://infra/measurement/HISTORY.md
  - id: openwiki-source-95fbd7291041b8b49b0243ab
    resource: repo://infra/measurement/measurement-b.md
  - id: openwiki-source-f6a618bf80edbe8b5d4671fd
    resource: repo://infra/measurement/README.md
  - id: openwiki-source-cfa41a0725950c63a0cb23e4
    resource: repo://infra/measurement/run-load-measurement.sh
  - id: openwiki-source-5b30546c1b94a01cd738da1f
    resource: repo://tests/unit/tools/test_measurement_b.py
generated: { by: "claude-code", at: "2026-11-20T11:18:18.429Z" }
---

# 부하 측정 도구

원본은 [infra/measurement/README.md](../../infra/measurement/README.md)와 측정 이력 [HISTORY.md](../../infra/measurement/HISTORY.md)다. 이 페이지와 다르면 원본이 맞다.

활동 분석 기능이 목표 규모(하루 활성 사용자 1천 명)를 버티는지 확인하려고 랩(EKS)에서 고정 부하를 걸고 API·consumer·DB를 잰다. 역할은 둘로 나뉜다.

- 랩을 띄우고 측정 Pod 권한을 주는 쪽: `infra/deployment`의 `measurement-*.sh`([AWS EKS 랩과 측정 세션](aws-eks-lab.md)).
- 측정 Pod 안팎에서 부하를 만들고 DB를 기록하는 쪽: `infra/measurement/`(이 페이지).

## 측정 종류

| 측정 | 기준 문서 | 무엇을 재나 |
| --- | --- | --- |
| 측정 A | [workload-137.6rps.md](../../infra/measurement/workload-137.6rps.md) | 1천 DAU 피크 고정 부하(조회 1.9 rps + 업로드 135.7묶음/s)에서 기준 사양(API 4·consumer 4·파티션 8·m7i.large·db.m7g.large)이 판정 C1~C6을 통과하는가 |
| 측정 B | [measurement-b.md](../../infra/measurement/measurement-b.md), `measurement-b.json` | 분석 API(`/api/v1/analytics/...`)와 일간 통계 batch를 켠 운영 구성에서도 기준 사양이 통과하는가, 구간이 많은 날의 조회 계산이 같은 API Pod의 다른 요청을 얼마나 늦추는가(G1) |
| 7B 계정 삭제 규모 | README 「7B 계정 삭제 운영 규모 측정」 | 운영 규모 데이터에서 계정 삭제(명시 순서 + 현지 날짜별 커밋)와 야간 이관 삭제가 걸리는 시간, 다른 사용자 조회·업로드를 막는가 |

판정 기준(측정 A·B 공통 C1~C6): 조회 p95 ≤ 300ms·p99 ≤ 500ms, 전체 오류율 ≤ 0.1%, 업로드 반영 지연 p95 ≤ 10초, 미처리 증가 추세 없음, k6 `dropped_iterations` 0, 도착률 목표 ±2%. 기준은 측정 전에 고정하고 측정 뒤 바꾸지 않는다.

## 고정 기준과 안전장치

- `run-load-measurement.sh`는 부하 정의 `workload-137.6rps.json`과 측정 B 기준 `measurement-b.json`의 sha256이 상수와 다르면 시작하지 않는다. k6 바이너리(v2.3.0)도 sha256이 고정값이 아니면 push에서 멈추고, 회수 묶음도 Pod 안 원본과 sha256을 비교한다.
- `EXPECT_HEAD`(기본 `c5d6e7f8a9b0`, analytics 테이블 마이그레이션)와 앱 이미지·DB·덤프의 alembic head가 모두 같아야 진행한다. 마이그레이션이 추가되면 다음 측정 전에 이 값과 `convert-seed-dump.sh`의 `EXPECT_TO`를 새 head로 바꾼다.
- 로컬 기록 폴더에는 계정 ID가 든 ECR URI가 남으므로 저장소 안 경로를 주면 스크립트가 멈춘다. 조회 토큰 `tokens.json`은 비밀값이라 회수 묶음에서 뺀다.
- `measurement-pod.sh`와 `run-load-measurement.sh`는 `DRY_RUN=1`이면 클러스터에 닿지 않고 실행할 명령만 출력한다.

## 측정 A 흐름

Mac에서 저장소 루트 기준으로 실행한다.

1. **덤프 변환**(스키마가 시드 덤프 뒤로 바뀌었을 때): `convert-seed-dump.sh`가 일회용 PostgreSQL에 복원하고 `alembic upgrade head` 뒤 새 덤프를 만든다. 기존 테이블 행 수가 같아야 한다.
2. **Pod 생성·push**: `measurement-pod.sh up`(loadgen 노드에 측정 Pod), `push`(하네스·재배치 SQL·덤프·head 파일을 `/work`로).
3. **`run-load-measurement.sh all`**: `check → push → prep → mixed → final → [post-dump] → fetch`.
   - `prep`: consumer 0 → `db-measurement-steps.sh` a0(관문) → a1(복원) → a2(시간순 재배치·VACUUM·inventory)
   - `mixed`: consumer 4 → 회차마다 질의 통계 초기화 → k6(`k6-mixed-read-upload.js`) → 유예 → 통계 기록 → 판정
   - `final`: 일간 통계 행 0(batch 미실행)과 표시 분포 기록, consumer 원래 수로 복구
4. **정리**: `measurement-pod.sh down`.

## 측정 B 흐름

`MEASUREMENT=b`를 주면 `measurement-pod-b.yaml`(일간 통계 job을 돌릴 `batch` 컨테이너를 더한 Pod)로 같은 순서를 돈다. 랩 RDS가 원본·작업 DB를 함께 담을 만큼(50GB 이상) 늘어나 있어야 하고, 남은 회차가 Asia/Seoul 자정 전에 끝나야 시작한다.

- **시작 상태**: 측정 A 덤프 복원 뒤 `dense_day_accounts.py`가 오늘 활동 구간이 정확히 1천·5천·1만 개인 계정을 넣고, 시드 계정 전원의 2026-11-12에 batch가 바로 처리할 표시를 넣는다(자정 직후 어제 날짜가 한꺼번에 batch 대상이 되는 상황). 이 상태를 원본 DB `mosemo_b_base`로 복사하고 회차마다 작업 DB를 원본에서 다시 만든다.
- **회차**: R1 R2 R3 R1 R2 R3, 회차마다 k6 900초(앞 180초 데우기 제외). R1은 측정 A 부하, R2는 일간 통계·세션 분석 조회를 더하고, R3는 구간 1만 계정의 오늘 조회(`heavy`)와 1천·5천 계정 탐침(`probe`)을 더한다. `heavy`·`probe`는 판정하지 않고 보고만 한다.
- **batch**: `analytics_measurement.py batch-loop`가 `analytics-daily-stats`만 k6 시작과 함께 한 번, 이후 300초마다 돌린다(운영 `serve`와 같은 주기). 라벨 제안 job은 외부 호출을 피하려고 돌리지 않는다.
- **G1 결론**(`summary-b.json`): R3 두 회 모두 통과면 `no_split_now`(계산을 스레드로 분리하지 않음), R3 실패·R2 두 회 통과면 `split_needed`, 그 밖은 `undecidable`.
- **보고**: API 로그 `analytics read` 줄의 `segment_count` 구간별 `source_read_ms`·`snapshot_build_ms`·`compute_ms` p50·p95와 무거운 사용자 비율별 환산표.
- 판정 로직(C1은 `read`·`stats`·`sessions` 각각, `heavy`·`probe`는 C1·C6 제외, G1 판정, 구간 수 분류)은 `tests/unit/tools/test_measurement_b.py`가 검증한다.

## 7B 계정 삭제 규모 측정

`run-delete-scale-measurement.sh all`이 측정 A 도구로 Pod·k6를 돌리며 7B 절차를 끼운다. 시드 대신 생성기(`delete-scale-generate.sql`)로 날짜 순서대로 모든 사용자를 섞어 넣어 한 사용자의 행이 운영처럼 여러 페이지에 흩어지게 한다(랩 기본 300명 × 91일, 약 157GB). 부하 없음(L1a)·부하만(r1)·삭제 + 야간 이관(r2, r3) 회차로 디스크 읽기·WAL·잠금 대기·VACUUM 지표를 남긴다. 생성이 끊기면 `RESUME=1`로 잇는다. 측정 뒤 RDS를 계속 쓸 때만 `delete-scale-steps.sh cleanup --yes`로 실험 인덱스·프로시저·생성 데이터를 지운다.

## 로컬 드라이런

랩 측정 전에 도구가 끝까지 도는지 Mac 일회용 컨테이너(PostgreSQL 18.3 + Kafka, 로컬 API·consumer)로 확인한다.

```sh
DUMP=<작은 변환 덤프> infra/measurement/local-dryrun.sh
MEASUREMENT=b infra/measurement/local-dryrun.sh
MEASUREMENT=delete-scale USERS=20 DAYS=3 MEASURE_DATE=<날짜> MIXED_SEC=60 infra/measurement/local-dryrun.sh
```

`k6_inputs_and_reports.py`의 `report-mixed`·`backlog-watch`는 실제 k6 결과가 있어야 돌아 옮긴 뒤 원본과 비교하지 않았으므로, 다음 랩 측정 전에 드라이런으로 먼저 확인한다.

## 측정 이력 요약

[HISTORY.md](../../infra/measurement/HISTORY.md)에 2026-11-13~17의 열 측정이 목표·방법·판정·결과·이후 결정과 함께 있다.

- 기준 사양 측정(2026-11-15): API 4·consumer 4·파티션 8로 C1~C6 합격, DB CPU 약 47%가 가장 많이 쓴 자원.
- 측정 A(2026-11-15~16, 다시 계산 표시 처리 뒤): 합격, DB CPU 약 30%. 표시 처리 질의가 업로드 경로 DB 실행 시간의 약 16%, 버퍼의 약 10%를 썼다. 이후 비교는 측정 A를 기준으로 삼는다.
- 7B 삭제 방식 로컬 측정: 야간 이관 삭제 127~150초, 90일 계정 삭제 10.6~17.9초, 라벨 FK 인덱스가 필요하고 주부하는 VACUUM.
- 측정 B(2026-11-17, 측정 전용 두 번째 랩 `mosemo-labb`): G1 판단 불가. R3(1만 구간 조회 추가)에서 다른 요청 p95가 조회 약 1.2초·업로드 약 1.3초로 올라 C1 실패했고, R2 한 회는 C4 실패였다. 일간 통계 한 번의 시간은 약 80%가 DB 읽기이고 구간 수에 비례했다. 일간 통계 batch 24번 중 9번이 `SourceSnapshot` 구간 겹침 검사의 `ValueError`로 실행 전체가 실패했다(측정용 합성 데이터 탓으로 판단했으나 데이터로 확인하지 않음). 이후 `SourceSnapshot` 검증 실패는 `InvalidSourceError`로 구분해 그 날짜만 실패로 기록하도록 바뀌어 한 날짜의 잘못된 원천이 실행 전체를 중단하지 않는다([활동 분석](../domain/activity-analytics.md)). 계산 분리 여부는 아직 결정되지 않았다.
- 7B 계정 삭제 운영 규모 측정(2026-11-16, AWS 랩, RDS gp3 250GB, 300명 × 91일): 무효. 생성기가 D일 기록을 미리 채워 k6 업로드가 `projection_time_regression`으로 거절되고 consumer가 같은 오류로 재시작을 반복해 부하가 약 80초 만에 중단됐다. 부하 없는 삭제 값은 남았다. 날짜별 분할의 마지막 트랜잭션(세션·영수증·라벨·기기·계정 삭제)이 881~1,635초 열려 VACUUM을 막았고, 기기를 참조하는 영수증·세션에 `device_id` 인덱스가 없어 기기 1행 삭제가 409~528초였다. 한 트랜잭션 방식은 야간 이관을 막아 기각 쪽이다. 생성기를 고쳐 다시 측정하고 삭제 방식과 P2 기준을 다시 정한다.

쿠버네티스 리소스 이름(Pod `step5-tools`, ServiceAccount `step5-operator`)과 Pod 안 경로(`/work/step5`)는 옛 이름 그대로다. `infra/deployment`의 RBAC·준비 점검 스크립트가 이 이름에 묶여 있기 때문이다.

## 관련 페이지

- [AWS EKS 랩과 측정 세션](aws-eks-lab.md)
- [활동 분석](../domain/activity-analytics.md)
- [테스트와 검증](../development/testing-and-verification.md)
