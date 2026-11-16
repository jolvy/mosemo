# 부하 측정 도구

랩(EKS)에서 1천 DAU 피크 고정 부하(137.6 rps)를 걸고 API·consumer·DB를 재는 도구다. 측정 A(7단계 뒤 기준 사양 재측정)에 쓴 도구를 역할이 드러나는 이름으로 옮겼다. 다음 측정과 비교도 이 도구로 한다. 지난 측정의 목표·방법·결과는 [HISTORY.md](HISTORY.md)에 있다.

랩을 띄우고 측정 Pod 권한을 주는 쪽은 [`infra/deployment`](../deployment/README.md)의 `measurement-*.sh`가 맡는다. 이 폴더는 측정 Pod 안팎에서 부하를 만들고 DB를 기록하는 쪽이다.

## 파일

| 파일 | 역할 |
| --- | --- |
| `run-load-measurement.sh` | 측정 전체 조율(Mac): 사전 확인, 도구 복사, DB 준비, k6 회차, 마무리 기록, 회수 |
| `db-measurement-steps.sh` | 측정 Pod 안 DB 쪽 절차: 관문, 덤프 복원, 시간순 재배치, VACUUM, inventory, 회차별 질의 통계 기록 |
| `convert-seed-dump.sh` | 시드 덤프를 새 스키마로 변환(일회용 로컬 PostgreSQL + `alembic upgrade head`) |
| `local-dryrun.sh` | 랩 없이 Mac 일회용 컨테이너로 같은 절차를 끝까지 도는 드라이런 |
| `measurement-pod.sh` | 측정 Pod 생성, 도구와 덤프 넣기, consumer 수 조정, 회수, 삭제 |
| `measurement-pod.yaml` | 측정 Pod 정의(tools·pg 컨테이너, `/work` 공유) |
| `db_harness.py` | DB 접속, SQL 파일 실행(`sql`), 계정·날짜 목록(`inventory`) |
| `reorder-segments-by-time.sql` | 구간·확정 테이블을 시간순으로 다시 써서 운영의 물리 배치를 흉내 냄 |
| `k6_inputs_and_reports.py` | k6 입력(부하 묶음, 토큰, 기기, context) 생성, 회차 판정, 밀린 묶음 감시 |
| `k6-common.js` | k6 공통 함수(조회 1건, 업로드 1건) |
| `k6-mixed-read-upload.js` | 조회+업로드 고정 비율 시나리오 |
| `workload-137.6rps.json` | 1천 DAU 피크 고정 부하 정의. `run-load-measurement.sh`가 sha256을 고정값과 비교한다 |
| `workload-137.6rps.md` | 같은 부하의 설명과 판정 기준 |
| `kubectl-chunked-cp.sh` | 큰 파일 `kubectl cp`를 2MB 조각 전송으로 바꾸는 대리 스크립트 |

## 대응표

옮기기 전 이름과 sha256이다. 측정 A 사전 등록이 도구를 이 sha256으로 고정했다. 옮기면서 파일 이름 참조, 저장소 경로 기본값, 하네스·Pod 스크립트의 쓰지 않는 명령, Python 검사 규칙 위반을 고쳤으므로 아래 세 파일을 뺀 나머지는 지금 sha256이 다르다.

| 옛 이름 | 원래 sha256 | 새 경로 |
| --- | --- | --- |
| `step7a-k6-limit.sh` | `ad0fb3b0cea9a95ef9b9be737fbd66053d8e0c62b0bb278813295bb2b8a809fc` | `infra/measurement/run-load-measurement.sh` |
| `step7a-run.sh` | `830a7ca65c57dd15a2690b66c1cbe44e57817d5e41c05ca20df0968467b8d600` | `infra/measurement/db-measurement-steps.sh` |
| `step7a-convert-dump.sh` | `cdaee58b45416fb9595628b7950ed6b9fcc637abfb2d34a6e6c62c62af252fe3` | `infra/measurement/convert-seed-dump.sh` |
| `step7a-local-dryrun.sh` | `5330737e09f1f54673eb8f7dbbf3eec37de678f49efca40efb99c83e3511d287` | `infra/measurement/local-dryrun.sh` |
| `step5-t0-kube.sh` | `6e67ea807f246099068577614041eb99a02074ed67c6e61071d7aaa3cb29de87` | `infra/measurement/measurement-pod.sh` |
| `step5-t0-pod.yaml` | `125694f827476f7bbed3614d4f54e5f17e43c8e01c9dba130187461d82c50564` | `infra/measurement/measurement-pod.yaml` |
| `step5-t0-harness.py` | `f214f1ac3336cf21c604be848ff6de133a49fc140e795283d4618302c079bceb` | `infra/measurement/db_harness.py` |
| `step5-t0-reorder.sql` | `e4b5e9cab31ccc2e4f70e0c472d0304ca3fad7e50c3e0e62966e570a4ac62b28` | `infra/measurement/reorder-segments-by-time.sql` |
| `step5-k6-prep.py` | `df0bf64cfb440f0aa91db56e28a6a81038f2aec7bd7e8a8e505b45b471a1fa47` | `infra/measurement/k6_inputs_and_reports.py` |
| `step5-k6-lib.js` | `91d12224567488c69fb4842218383ef8131deb9b6e8061210da7ebaa2240b9f7` | `infra/measurement/k6-common.js` |
| `step5-k6-mixed.js` | `8f6bcb731f64417c937f8460b127672cd70a7825b8cdb611c28ccbe8eb80e780` | `infra/measurement/k6-mixed-read-upload.js` |
| `workload-137.6rps.json` | `05eb76f76af02112ae556de589c3832ef6c8b4eb5bc0e05fc9fb892444d22d29` | `infra/measurement/workload-137.6rps.json` |
| `workload-137.6rps.md` | `3bb85dae533da6af9b03b711041c93fd44d317eac8cb5a6779ecade913607065` | `infra/measurement/workload-137.6rps.md` |
| `kubectl-chunk-shim.sh` | `893fb153bca53525b95edb02dc4a57b183e31a71c9567fb1f77428c0a7aea7f6` | `infra/measurement/kubectl-chunked-cp.sh` |

`workload-137.6rps.json`, `reorder-segments-by-time.sql`, `kubectl-chunked-cp.sh`는 내용을 바꾸지 않았다(sha256 같음). 도구가 `workload-${총 rps}rps.json` 규칙으로 이름을 계산하므로 이름도 그대로다.

옮기지 않은 것: 5단계 분석 원형 측정 전용 스크립트, 측정 A로 대체된 5단계 판, k6 실행 파일, 생성 데이터·결과(부하 묶음, 토큰, 덤프, 결과 묶음), 사전 등록·보고 문서. 주석의 `STEP5-PLAN`, `STEP5-T0-PREREG`, `STEP5-T0-REPORT`, `STEP7A-PREREG` 같은 이름은 저장소 밖에 보관한 사전 등록·보고 문서를 가리킨다.

쿠버네티스 리소스 이름(Pod `step5-tools`, ServiceAccount `step5-operator`)과 Pod 안 경로(`/work/step5`, `/work/repo`)는 옛 이름 그대로다. `infra/deployment`의 RBAC·준비 점검 스크립트가 이 이름에 묶여 있다. 실행 기록 이름(`step7a.log`, `step7a-<UTC>.tar.gz`, `post-7a.dump`)도 그대로다.

## 저장소 밖 준비물

| 준비물 | 기본 위치 | 설명 |
| --- | --- | --- |
| k6 v2.3.0 linux-amd64 | `~/mosemo-step5/k6/v2.3.0/k6-v2.3.0-linux-amd64/k6`(`K6_LOCAL`) | 측정 Pod로 복사한다. sha256 `e001bbf39ba67424a85c9c8516440ba5c73ecf7a6021be96c76194380c15f260`이 아니면 `run-load-measurement.sh push`가 멈춘다 |
| k6 v2.3.0 macos-arm64 | `~/mosemo-step5/k6/v2.3.0/k6-v2.3.0-macos-arm64/k6` | `local-dryrun.sh`가 쓴다(고정 경로) |
| 시드 덤프 | `~/mosemo-step5/seed.dump`, head 파일 `~/mosemo-step5/s1-alembic.txt`(`DUMP`, `ALEMBIC_FILE`) | `scripts/seed_load.py`로 만든 DB를 `pg_dump -Fc`로 뜬 것. 스키마가 바뀌었으면 `convert-seed-dump.sh`로 먼저 변환한다 |
| 측정 kubeconfig | `~/.mosemo/lab/measurement.kubeconfig`(`MEASUREMENT_KUBECONFIG`) | `infra/deployment/measurement-*.sh`가 만든다. 두 스크립트의 kubectl은 이것만 쓴다 |
| 랩 매니페스트 | `~/.mosemo/lab/measurement/manifest.json`(`MANIFEST`) | `measurement-pod.sh up`이 `app_image.uri`·`digest`를 읽는다 |
| 로컬 기록 폴더 | `~/mosemo-step5/kube`(`measurement-pod.sh`), `~/mosemo-step5/kube-7a`(`run-load-measurement.sh`)(`STATE`) | 로그, consumer 원래 replicas, 회수 묶음 |
| 드라이런 결과 | `~/mosemo-step5/dryrun-7a-<UTC>/` | `local-dryrun.sh`가 만든다(고정 경로) |
| Docker | — | `convert-seed-dump.sh`, `local-dryrun.sh`가 일회용 컨테이너(`m7a-conv-<꼬리>`, `m7a-rds`, `m7a-kafka`)를 만들고 끝나면 볼륨까지 지운다 |

`kubectl-chunked-cp.sh`는 macOS와 Homebrew kubectl(`/opt/homebrew/bin/kubectl`)을 전제로 한다(`stat -f`, 고정 경로). 큰 덤프를 `kubectl cp`로 넣다 끊길 때, `kubectl` 이름의 심볼릭 링크로 PATH 앞에 둔다. `cp`만 2MB 조각 전송으로 바꾸고 나머지 명령은 진짜 kubectl로 넘긴다.

```sh
mkdir -p ~/.local/kubectl-chunked
ln -sf "$PWD/infra/measurement/kubectl-chunked-cp.sh" ~/.local/kubectl-chunked/kubectl
export PATH="$HOME/.local/kubectl-chunked:$PATH"
```

## 실행 순서

랩 측정은 Mac에서 저장소 루트 기준으로 실행한다. `measurement-pod.sh`와 `run-load-measurement.sh`는 `DRY_RUN=1`이면 클러스터에 닿지 않고 실행할 명령만 출력한다. `convert-seed-dump.sh`와 `local-dryrun.sh`는 `DRY_RUN`을 읽지 않아 늘 실제로 실행한다. `local-dryrun.sh`에 `DRY_RUN=1`을 주면 하위 `run-load-measurement.sh`만 출력 모드가 되어, 컨테이너·API·consumer는 실제로 뜨지만 복원·k6·회수 묶음·측정 직후 덤프가 생기지 않으므로 주지 않는다.

1. **덤프 변환**(스키마가 시드 덤프 뒤로 바뀌었을 때): 일회용 로컬 PostgreSQL에 복원하고 이 저장소 코드로 `alembic upgrade head`를 한 뒤 새 덤프를 만든다. 기존 테이블 행 수가 변환 전후로 같아야 한다.
   ```sh
   infra/measurement/convert-seed-dump.sh <원래 덤프> <새 덤프> <이름 꼬리>
   ```
   결과: `<새 덤프>`, `.sha256`, `<새 덤프 이름>.alembic.txt`, `.counts.txt`, `.convert.log`
2. **Pod 생성·push**: 랩의 측정 준비가 끝난 뒤 측정 Pod를 loadgen 노드에 만들고 하네스·재배치 SQL·덤프·alembic head 파일을 `/work`로 넣는다.
   ```sh
   LOADGEN_NODE=<노드 이름> infra/measurement/measurement-pod.sh up
   DUMP=<새 덤프> ALEMBIC_FILE=<새 덤프 이름>.alembic.txt infra/measurement/measurement-pod.sh push
   ```
3. **DB 절차 → k6 회차 → 마무리 → 회수**: `all`이 `check → push → prep → mixed → final → [post-dump] → fetch`를 차례로 돈다. 단계별로 따로 부를 수도 있다(`check`, `push`, `prep`, `mixed`, `final`, `post-dump`, `fetch`, `fetch-dump`).
   ```sh
   DEADLINE_UTC=<잠금 토큰 만료 전 UTC> infra/measurement/run-load-measurement.sh all
   ```
   - `prep`: consumer 0 → `db-measurement-steps.sh a0`(관문, 복원 전) → `a1`(복원, pg 컨테이너) → `a2`(관문·재배치·VACUUM·inventory)
   - `mixed`: consumer `CONSUMERS`개 → 회차마다 `rec before-…`(질의별 통계 초기화) → k6 → 유예 → `rec after-…` → `report-mixed` 판정
   - `final`: 일간 행 0(batch 미실행)·표시 분포 기록, consumer를 원래 수로 되돌림
4. **Pod 삭제**: `infra/measurement/measurement-pod.sh down`. `measurement-pod.sh fetch`는 Pod 안 `/work/results` 전체를 따로 회수할 때 쓴다.

**로컬 드라이런**: 랩 없이 같은 순서를 Mac 일회용 컨테이너(PostgreSQL 18.3 + Kafka, 로컬 API·consumer)로 끝까지 돈다. 랩 측정 전에 도구가 끝까지 도는지 확인하는 용도다.

```sh
DUMP=<작은 변환 덤프> infra/measurement/local-dryrun.sh
```

## 환경 변수

| 변수 | 쓰는 곳 | 기본값·뜻 |
| --- | --- | --- |
| `MEASUREMENT_KUBECONFIG`, `MANIFEST`, `LOADGEN_NODE` | `measurement-pod.sh` | 위 「저장소 밖 준비물」, loadgen 노드 이름(노드 조회 권한이 없을 때 확인용) |
| `DUMP`, `ALEMBIC_FILE`, `STATE`, `DRY_RUN` | `measurement-pod.sh` | 덤프·head 파일, 기록 폴더, `1`이면 출력만 |
| `DEADLINE_UTC` | `run-load-measurement.sh` | 기본값 없음(랩). 잠금 토큰 만료 전 시각. 회차가 이 시각 전에 끝나지 않으면 건너뛴다 |
| `MIXED_TOTAL`, `MIXED_SEC`, `MIXED_RUNS`, `MIXED_WARM`, `MIXED_GRACE` | `run-load-measurement.sh` | 137.6 rps, 900초, 2회, 웜업 60초, 유예 60초 |
| `CONSUMERS`, `PARTITIONS`, `POST_DUMP`, `K6_LOCAL` | `run-load-measurement.sh` | consumer 4, 파티션 8, 측정 직후 덤프 0(끔), 로컬 k6 경로 |
| `EXPECT_HEAD` | `run-load-measurement.sh`, `db-measurement-steps.sh` | `c5d6e7f8a9b0`. 앱 이미지·DB·덤프의 alembic head가 모두 이 값이어야 진행한다 |
| `LOCAL`, `W`, `ENV_FILE`, `PY`, `K6_BIN`, `API_URL`, `S5_REPO` | `run-load-measurement.sh`(`LOCAL=1`) | 드라이런용. `local-dryrun.sh`가 준다. `S5_REPO` 기본은 이 저장소 루트(Pod 안은 `/work/repo`) |
| `TARGET`, `END_DATE`, `RDS_PG` | `db-measurement-steps.sh` | `eks`(Pod 안) 또는 `local`, 조회 부하의 "오늘" `2026-11-12`, 드라이런 DB 컨테이너 |
| `REPO`, `PORT`, `EXPECT_FROM`, `EXPECT_TO` | `convert-seed-dump.sh` | 이 저장소 루트, 55471, `a3b4c5d6e7f8`, `c5d6e7f8a9b0` |
| `REPO`, `LOCAL_CONSUMERS` | `local-dryrun.sh` | 이 저장소 루트, 로컬 consumer 1개 |
| `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USER`, `DB_PASSWORD` 또는 `S5_DSN` | `db_harness.py`, `k6_inputs_and_reports.py` | DB 접속. Pod 안은 앱 configmap·Secret에서 받는다 |
| `PGSSLMODE`, `PGSSLROOTCERT` | `db_harness.py` | `verify-full`이면 RDS CA로 검증한다 |
| `S5_REQ_TIMEOUT`, `S5_LONG_TIMEOUT`, `S5_SQL_TIMEOUT`, `S5_CMD_TIMEOUT` | `db_harness.py` | 요청 30초, 긴 단위 120초, SQL 문장 600초(`a2`는 7200초), `inventory` 전체 1800초 |
| `S5_RSS_LOG` | `db_harness.py` | 있으면 모드별 최대 RSS를 이 파일에 덧붙인다 |
| `AUTH_JWT_SECRET_KEY`, `AUTH_JWT_ISSUER`, `AUTH_JWT_AUDIENCE` | `k6_inputs_and_reports.py` | 조회 토큰 서명. Pod 안은 앱 Secret에서 받는다. 만든 `tokens.json`은 비밀값이라 회수 묶음에서 뺀다 |

## alembic head가 바뀌면

`EXPECT_HEAD`(기본 `c5d6e7f8a9b0`)와 `convert-seed-dump.sh`의 `EXPECT_TO`는 측정 A 때의 마이그레이션 head다. 마이그레이션이 추가되면 다음 측정 전에 두 값을 새 head로 바꾸거나 환경 변수로 준다. 그렇지 않으면 `db-measurement-steps.sh a0`·`a2`의 head 관문과 덤프 변환이 멈춘다.

## 확인하지 않은 경로

`k6_inputs_and_reports.py`의 `report-mixed`·`backlog-watch`는 실제 k6 회차 결과가 있어야 돌아서, 옮긴 뒤 원본과 비교하지 않았다. 다음 랩 측정 전에 `local-dryrun.sh`로 먼저 확인한다.
