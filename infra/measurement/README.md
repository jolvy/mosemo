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
| `run-delete-scale-measurement.sh` | 7B 계정 삭제 운영 규모 측정 조율(Mac): 측정 A 도구로 Pod·k6를 돌리고 7B 절차를 끼운다 |
| `delete-scale-steps.sh` | 7B 측정 Pod 안 DB 쪽 절차(pg 컨테이너, psql): 준비, 생성, 생성 뒤 확인, 삭제 실행·표본, 회차 연결, 검사, 정리 |
| `delete-scale-generate.sql` | 7B 생성기 프로시저: 날짜 순서대로 그날 모든 사용자를 시각순으로 섞어 넣는다 |
| `delete-scale-prepare.sql` | 7B 생성 전 준비: 인덱스 후보 6개, 4개 표 autovacuum 기준 1% |
| `account-delete-procedure.sql` | 계정 삭제 후보 프로시저(명시 순서 한 트랜잭션 / 레코드 현지 날짜별 커밋) |
| `archive-delete-procedure.sql` | 야간 이관 DB 삭제 흉내 프로시저(레코드 2,000행씩 커밋) |
| `delete-scale-local-check.sh` | 7B 생성기 로컬 검사: 일회용 PostgreSQL에서 행 수·연결 위반·흩어짐·문맥 모델 검증 |
| `measurement-b.json` | 측정 B(새 분석 API·일간 통계 batch) 고정 기준: 회차·도착률·판정·가정. `run-load-measurement.sh`가 sha256을 고정값과 비교한다 |
| `measurement-b.md` | 같은 기준의 설명(질문, 회차, 시작 상태, batch, 판정, 보고, 가정, 전제) |
| `measurement-pod-b.yaml` | 측정 B Pod 정의: `measurement-pod.yaml`에 일간 통계 job을 돌릴 `batch` 컨테이너를 더한 것 |
| `dense_day_accounts.py` | 측정 B: 오늘 활동 구간이 정확히 1천·5천·1만 개인 계정을 DB에 바로 넣는다 |
| `analytics_measurement.py` | 측정 B: 새 API 토큰(`tokens-b`), batch 반복 실행(`batch-loop`), 회차 판정(`report-b`), 전체 요약(`summary-b`) |
| `k6-analytics-mixed.js` | 측정 B 시나리오: 고정 부하(read·write)에 일간 통계·세션 분석·구간 많은 날 조회를 회차 종류별로 더한다 |

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

`workload-137.6rps.json`, `reorder-segments-by-time.sql`, `kubectl-chunked-cp.sh`는 #305로 옮길 때는 내용을 바꾸지 않았다(그때 sha256 같음). `kubectl-chunked-cp.sh`는 그 뒤 #306에서 원격 경로를 위치 인자로 넘기도록 고쳤다. 도구가 `workload-${총 rps}rps.json` 규칙으로 이름을 계산하므로 이름도 그대로다.

7B 계정 삭제 측정 도구의 저장소 밖 원본(#308)이다. sha256은 옮기기 전 값이다. 원본 셸 스크립트 네 개는 `delete-scale-steps.sh` 한 파일의 하위 명령으로 합쳤다.

| 옛 이름 | 원래 sha256 | 새 경로 |
| --- | --- | --- |
| `gen.sql` | `9360995a455e4483779d72f2dcf4626cead42163e70f77e9a3731c66ad8a556c` | `infra/measurement/delete-scale-generate.sql` |
| `00_prep.sql` | `007b2a67419777a234a1aa7cde2be54ba68de5d5e53ecbe3495f2f5d46c3e103` | `infra/measurement/delete-scale-prepare.sql` |
| `20_account_delete.sql` | `a4e06ecdfe4781255e9db23e502890144f0a7bef908ad68deda69f3d7f0fd817` | `infra/measurement/account-delete-procedure.sql` |
| `10_archive_delete.sql` | `d9a07508f052b4187d71e4fab3087bd14293105d82f0ba89c71980695619cd0c` | `infra/measurement/archive-delete-procedure.sql` |
| `lab/gen_lab.sh` | `cd4a8a3c72611c712ae3805b5be77ff1e403ff2d93db8bb462d5b133aba4a7b6` | `infra/measurement/delete-scale-steps.sh` |
| `lab/lab_run.sh` | `e708047db3a943c1002bd424516b725f6072337372230c9d8a9137047f0feea4` | `infra/measurement/delete-scale-steps.sh` |
| `lab/sampler.sh` | `8a3ac0e263908690011e323c346988c05dcae77deb3aa9dac7b815a430044986` | `infra/measurement/delete-scale-steps.sh` |
| `lab/x7b_hooks.sh` | `7e740183d8e0ed08e2a6d76c0ceca5ba71b55f8b7c6397bd9a97707d9131224e` | `infra/measurement/delete-scale-steps.sh` |

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
| 로컬 기록 폴더 | `~/mosemo-step5/kube`(`measurement-pod.sh`), `~/mosemo-step5/kube-7a`(`run-load-measurement.sh`)(`STATE`) | 로그, consumer 원래 replicas, 회수 묶음. 계정 ID가 든 ECR URI가 남으므로 저장소 안 경로를 주면 두 스크립트가 멈춘다 |
| 드라이런 결과 | `~/mosemo-step5/dryrun-7a-<UTC>/` | `local-dryrun.sh`가 만든다(고정 경로) |
| Docker | — | `convert-seed-dump.sh`, `local-dryrun.sh`가 일회용 컨테이너(`m7a-conv-<꼬리>`, `m7a-rds`, `m7a-kafka`)를 만들고 끝나면 볼륨까지 지운다 |

`kubectl-chunked-cp.sh`는 macOS와 Homebrew kubectl(`/opt/homebrew/bin/kubectl`)을 전제로 한다(`stat -f`, 고정 경로). 큰 덤프를 `kubectl cp`로 넣다 끊길 때, `kubectl` 이름의 심볼릭 링크로 PATH 앞에 둔다. `cp`만 2MB 조각 전송으로 바꾸고 나머지 명령은 진짜 kubectl로 넘긴다. 측정과 상관없는 `kubectl cp`까지 가로채지 않도록 PATH는 측정 셸에서만 바꾸고, 셸 시작 파일(`~/.zshrc` 등)에 넣지 않는다.

```sh
# 측정 셸에서만 실행한다
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

**측정 B**(`measurement-b.md`): `MEASUREMENT=b`를 주면 `measurement-pod.sh`는 `measurement-pod-b.yaml`로 Pod를 만들고, `run-load-measurement.sh all`은 다음을 돈다. 랩 RDS 저장 공간이 원본·작업 DB를 함께 담을 만큼(50GB 이상) 늘어나 있어야 하고, 남은 회차가 Asia/Seoul 자정 전에 끝나야 한다(아니면 시작하지 않는다).

```sh
MEASUREMENT=b LOADGEN_NODE=<노드 이름> infra/measurement/measurement-pod.sh up
MEASUREMENT=b DUMP=<새 덤프> ALEMBIC_FILE=<새 덤프 이름>.alembic.txt infra/measurement/measurement-pod.sh push
MEASUREMENT=b DEADLINE_UTC=<잠금 토큰 만료 전 UTC> infra/measurement/run-load-measurement.sh all
```

- 자정 검사 → `check` → `push` → `prep`: a0·a1·a2 → `b-perm`(DB를 만들 권한·작업 DB 소유자) → `b-seed`(구간 많은 계정, 2026-11-12 표시, VACUUM) → `b-base`(원본 DB `mosemo_b_base`로 복사)
- `mixed`: 회차마다 자정 검사 → consumer 0 → `b-reset`(작업 DB를 원본에서 다시 만듦) → `b-round-start` → consumer 4 → batch 반복(`batch` 컨테이너) + k6(`k6-analytics-mixed.js`) → 유예 → API 로그의 `analytics read` 줄 회수 → `report-b`
- `final`: `b-final`(일간 행·표시·크기, 원본 DB 삭제) → `summary-b`(G1 결론, 구간 수별 조회 시간, 환산표). 중간에 멈추면 작업 DB가 없을 때 원본에서 다시 만들고 결과를 회수한다
- 결과: `k6out/mixed-b-r<n>.json·.md`, `k6out/summary-b.json·.md`, `results/b-dense.txt`, `b-reset.txt`, `b-batch.txt`, `b-round-start.json`, `b-final.txt`

**로컬 드라이런**: 랩 없이 같은 순서를 Mac 일회용 컨테이너(PostgreSQL 18.3 + Kafka, 로컬 API·consumer)로 끝까지 돈다. 랩 측정 전에 도구가 끝까지 도는지 확인하는 용도다.

```sh
DUMP=<작은 변환 덤프> infra/measurement/local-dryrun.sh
MEASUREMENT=b infra/measurement/local-dryrun.sh   # 측정 B 6회차(회차 60초, 되돌리기 상한 120초)
```

## 환경 변수

| 변수 | 쓰는 곳 | 기본값·뜻 |
| --- | --- | --- |
| `MEASUREMENT_KUBECONFIG`, `MANIFEST`, `LOADGEN_NODE` | `measurement-pod.sh` | 위 「저장소 밖 준비물」, loadgen 노드 이름(노드 조회 권한이 없을 때 확인용) |
| `DUMP`, `ALEMBIC_FILE`, `STATE`, `DRY_RUN` | `measurement-pod.sh` | 덤프·head 파일, 기록 폴더, `1`이면 출력만 |
| `DEADLINE_UTC` | `run-load-measurement.sh` | 기본값 없음(랩). 잠금 토큰 만료 전 시각. 회차가 이 시각 전에 끝나지 않으면 건너뛴다 |
| `MIXED_TOTAL`, `MIXED_SEC`, `MIXED_RUNS`, `MIXED_WARM`, `MIXED_GRACE` | `run-load-measurement.sh` | 137.6 rps, 900초, 2회, 웜업 60초, 유예 60초 |
| `CONSUMERS`, `PARTITIONS`, `POST_DUMP`, `K6_LOCAL` | `run-load-measurement.sh` | consumer 4, 파티션 8, 측정 직후 덤프 0(끔), 로컬 k6 경로 |
| `EXCLUDE_ACCOUNTS` | `run-load-measurement.sh` | 기본 빈 값. 쉼표로 이은 계정 ID를 장기 계정과 함께 k6 토큰·업로드 대상에서 뺀다 |
| `EXPECT_HEAD` | `run-load-measurement.sh`, `db-measurement-steps.sh` | `c5d6e7f8a9b0`. 앱 이미지·DB·덤프의 alembic head가 모두 이 값이어야 진행한다 |
| `LOCAL`, `W`, `ENV_FILE`, `PY`, `K6_BIN`, `API_URL`, `S5_REPO` | `run-load-measurement.sh`(`LOCAL=1`) | 드라이런용. `local-dryrun.sh`가 준다. `S5_REPO` 기본은 이 저장소 루트(Pod 안은 `/work/repo`) |
| `TARGET`, `END_DATE`, `RDS_PG` | `db-measurement-steps.sh` | `eks`(Pod 안) 또는 `local`, 조회 부하의 "오늘" `2026-11-12`, 드라이런 DB 컨테이너 |
| `REPO`, `PORT`, `EXPECT_FROM`, `EXPECT_TO` | `convert-seed-dump.sh` | 이 저장소 루트, 55471, `a3b4c5d6e7f8`, `c5d6e7f8a9b0` |
| `REPO`, `LOCAL_CONSUMERS` | `local-dryrun.sh` | 이 저장소 루트, 로컬 consumer 1개 |
| `MEASUREMENT` | `run-load-measurement.sh`, `measurement-pod.sh`, `local-dryrun.sh` | `a`(기본, 측정 A) 또는 `b`(측정 B). `local-dryrun.sh`는 `delete-scale`도 받는다 |
| `MEASUREMENT_NOW_KST`, `RESET_LIMIT_S`, `API_LOG`, `LOCAL_CONSUMER_STOP`, `LOCAL_CONSUMER_START` | `run-load-measurement.sh`(측정 B) | 자정 검사의 현재 시각(ISO 8601), 되돌리기 SQL 상한(랩은 `measurement-b.json` 600초, `LOCAL=1`만 덮음), 로컬 API 로그, 로컬 consumer 멈춤·시작 명령 |
| `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USER`, `DB_PASSWORD` 또는 `S5_DSN` | `db_harness.py`, `k6_inputs_and_reports.py` | DB 접속. Pod 안은 앱 configmap·Secret에서 받는다 |
| `PGSSLMODE`, `PGSSLROOTCERT` | `db_harness.py` | `verify-full`이면 RDS CA로 검증한다 |
| `S5_REQ_TIMEOUT`, `S5_LONG_TIMEOUT`, `S5_SQL_TIMEOUT`, `S5_CMD_TIMEOUT` | `db_harness.py` | 요청 30초, 긴 단위 120초, SQL 문장 600초(`a2`는 7200초), `inventory` 전체 1800초 |
| `S5_RSS_LOG` | `db_harness.py` | 있으면 모드별 최대 RSS를 이 파일에 덧붙인다 |
| `AUTH_JWT_SECRET_KEY`, `AUTH_JWT_ISSUER`, `AUTH_JWT_AUDIENCE` | `k6_inputs_and_reports.py` | 조회 토큰 서명. Pod 안은 앱 Secret에서 받는다. 만든 `tokens.json`은 비밀값이라 회수 묶음에서 뺀다 |

## 7B 계정 삭제 운영 규모 측정

7B 계정 삭제(명시 순서 + 레코드 현지 날짜별 커밋)가 운영 규모 RDS에서 몇 분 걸리고, 동시에 도는 야간 이관 삭제의 VACUUM과 다른 사용자 조회·업로드를 막는지 잰다. 시드 대신 생성기로 데이터를 만든다. 시드는 계정 단위로 행이 붙어 있어 한 사용자의 행이 운영처럼 여러 페이지에 흩어지지 않는다.

- 데이터: 측정 날(KST)을 D라 할 때 `USERS`명 × `DAYS`일, 첫날은 항상 D−90이다. 랩 기본 300명 × 91일(약 157GB, 로컬 300명 × 3일 5.2GB에서 추정). 사용자 하루당 레코드 5,500, 활동 구간 410 + 공백 1, 제안 약 433, 확정 약 236, 영수증 2,934, 세션 3. 계정당 장치 1, 라벨 6.
- 순서(`run-delete-scale-measurement.sh all`): `check` → push(`measurement-pod.sh push --no-dump`, `run-load-measurement.sh push`, 7B 파일) → `a0`(빈 DB 관문) → 시간 검사(`USERS × DAYS × SEC_PER_USER_DAY` + 1.5시간이 `DEADLINE_UTC` 안) → `prepare` → `generate` → `after-generate`(ANALYZE, 제약 유효성, 행 수, 4개 표 VACUUM 1회, 통계 초기화) + inventory(`--today D`) → L1a → hooks → `mixed`(3회차) → hooks 종료 대기 → `final` → `fetch`
- 삭제와 회차: 삭제 대상은 사용자 3·4·5(`x7b_gu.u`)이고 `EXCLUDE_ACCOUNTS`로 k6에서 뺀다.
  - L1a = 부하 없이 사용자 3을 `'day'`로 삭제(기준 읽기 수)
  - r1 = L0(부하만)
  - r2 = L1: 사용자 4를 `'day'`로 삭제 + 같은 때 야간 이관 D−90
  - r3 = L2: 사용자 5를 `'one'`으로 삭제 + 야간 이관 D−89. "L1 끝"과 "r3 시작 + 웜업 + `HOOK_GAP`" 중 늦은 때 시작한다. r3가 이미 끝났으면 부하 없이 돌고 `load=none`을 남긴다.
- 결과: `/work/results/x7b/<이름>/`(기존 `fetch` 묶음에 들어간다). `summary.txt`에 디스크 읽기(힙·인덱스, `pg_statio` 전후 차이)·레코드 1행당 읽기·WAL, 단계별 시간, 가장 긴 트랜잭션 나이·`age(backend_xmin)`, 1초 넘은 잠금 대기 수, VACUUM의 "dead but not yet removable" 합계가 있다. 운영 시간은 읽기 수 ÷ IOPS로 낸다. `pg_statio` 읽기는 OS 페이지 캐시 적중을 구분하지 않으므로 RDS를 재부팅하지 않는다.
- 생성이 끊기면 `RESUME=1 run-delete-scale-measurement.sh generate`로 마지막 묶음 다음부터 잇고(`a0`·`prepare`를 건너뜀) `after`·`l1a`·`measure`·`fetch`를 차례로 부른다.
- 검사: `delete-scale-local-check.sh`(생성기), `MEASUREMENT=delete-scale USERS=20 DAYS=3 MEASURE_DATE=<날짜> MIXED_SEC=60 local-dryrun.sh`(전체 순서).
- 정리: 측정 뒤 RDS를 그대로 쓸 때만 pg 컨테이너에서 `delete-scale-steps.sh cleanup --yes`를 실행한다. 실험 인덱스 6개·설정·프로시저·`x7b_*` 표와 생성 데이터를 지운다. `all`에는 들어 있지 않다.

| 변수 | 기본값·뜻 |
| --- | --- |
| `MEASURE_DATE` | 필수. 측정 날 D(KST). 생성 첫날 D−90, "오늘 화면"의 오늘 |
| `USERS`, `DAYS` | 300, 91 |
| `SEC_PER_USER_DAY` | 0.66. 생성 시간 검사용(로컬 300명 실측 0.33초의 2배) |
| `MIXED_WARM`, `HOOK_GAP` | 60, 30. 회차 시작 뒤 삭제를 시작하기까지 기다리는 초 |
| `RESUME` | 1이면 `a0`·`prepare`를 건너뛰고 생성을 잇는다 |
| `STATE` | `~/mosemo-step5/kube-delete-scale` |

## alembic head가 바뀌면

`EXPECT_HEAD`(기본 `c5d6e7f8a9b0`)와 `convert-seed-dump.sh`의 `EXPECT_TO`는 측정 A 때의 마이그레이션 head다. 마이그레이션이 추가되면 다음 측정 전에 두 값을 새 head로 바꾸거나 환경 변수로 준다. 그렇지 않으면 `db-measurement-steps.sh a0`·`a2`의 head 관문과 덤프 변환이 멈춘다.

## 확인하지 않은 경로

`k6_inputs_and_reports.py`의 `report-mixed`·`backlog-watch`는 실제 k6 회차 결과가 있어야 돌아서, 옮긴 뒤 원본과 비교하지 않았다. 다음 랩 측정 전에 `local-dryrun.sh`로 먼저 확인한다.
