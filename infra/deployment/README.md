# AWS 배포 절차와 검증

이 환경은 운영 복제 환경이며 작업 세션마다 띄우고 내린다. 세션 시작·배포·종료는
아래 [작업 세션 절차](#작업-세션-절차)의 스크립트로 한다. 실제 apply·ECR push·Kubernetes 적용은
별도 승인 후에만 실행한다.

## 이미지와 프로세스

Dockerfile은 Python 3.14·uv 0.11.21로 locked 설치하고 UID/GID 10001로 실행한다.
`.dockerignore`의 허용 목록에는 소스·migration·빌드 입력만 포함된다. 환경 파일,
Terraform state·plan, 로컬 handoff·Secret은 이미지에 복사하지 않는다.

| 프로세스 | manifest | 실행 |
| --- | --- | --- |
| API | deployment.yaml | uvicorn mosemo.entrypoints.api.main:app, port 8000 |
| Activity consumer | consumer-deployment.yaml | python -m mosemo.entrypoints.consumer |
| Batch jobs (label proposals) | batch-deployment.yaml | python -m mosemo.entrypoints.batch serve |
| Migration | migration-job.yaml | alembic upgrade head |
| Lab Kafka | kafka.yaml | StatefulSet 1개, cp-kafka 7.8.0, KRaft broker/controller |
| Topic 초기화 | kafka-init-job.yaml | 목표 파티션 `KAFKA_TOPIC_PARTITIONS`(일반 4, 측정 프로필 8; 기존 토픽이 더 적으면 목표까지 늘리고 줄이지 않음), replication factor 1 |

API·consumer·batch·migration은 **같은 새 이미지 digest**를 사용한다. 기존 삭제한 ECR
이미지의 digest는 재사용할 수 없다. batch는 label proposal 작업을 시작 시와 5분마다 실행하는
스케줄러 1개다. `LABEL_MODEL`(ConfigMap, `google:gemini-3.1-flash-lite`)과 `GOOGLE_API_KEY`(Secret)를 쓴다.
같은 작업이 두 프로세스에서 돌지 않도록 replicas=1, maxSurge=0이다. HTTP Service·probe는 없고,
deploy.sh는 시작 직후 실행한 작업의 성공 로그로 확인한다. 측정 프로필 세션에서는 batch를 띄우지 않는다
([5단계 측정 세션](#5단계-측정-세션)).

API와 consumer는 같은 DB·BATCH_TOPIC·BATCH_GROUP_ID·Kafka 주소를 사용한다.
consumer에는 HTTP Service·가짜 HTTP probe를 붙이지 않는다. consumer Pod Running은
구독·DB 처리 성공의 증거가 아니므로 실제 메시지→DB 저장과 group/offset을 확인한다.
consumer는 형식 오류·영구 실패 배치(WARNING)와 분류에 없는 예외(ERROR)를 로그로 남기고 ACK한 뒤
건너뛴다. 일시 실패(계정 잠금 초과, DB 오류, 연결 풀 대기 초과, `OSError`)는 프로세스 안에서 1·2·4·8초
간격으로 총 5회 시도하고, 모두 실패하면 ACK 없이 프로세스를 종료해 Kubernetes가 재시작한다
([ADR 0006](../../docs/adr/0006-deliver-activity-batches-through-kafka.md) 「처리 실패 정책」). 종료 유예 45초는
consumer의 30초 drain보다 길게 두었다. API probes는 /openapi.json만 확인한다.

## 노드 배치와 자원

노드는 t3.small 2대가 기본이다(측정 프로필은 아래 [5단계 측정 세션](#5단계-측정-세션)). Kafka 전용 그룹 1대(ap-southeast-2a, taint `dedicated=kafka:NoSchedule`)와
API·consumer·batch·migration·시스템 add-on이 함께 쓰는 app 그룹 1~2대(2a·2b)다. Kafka StatefulSet만
`pool: kafka` nodeSelector와 toleration을 가진다. API·consumer는 각각 224Mi를 요청하고 limit 512Mi,
batch는 160Mi 요청(로컬 유휴 실측 118MiB)·limit 512Mi, Kafka는 512Mi 요청·768Mi limit, heap max 256Mi, log cleaner dedupe buffer 32Mi다.
topic Job은 request 128Mi/limit 256Mi다. requests는 첫 세션 실측 후 재산정한다.

- PriorityClass(`priority-classes.yaml`): `mosemo-api`(30000) > `mosemo-consumer`(20000) >
  `mosemo-batch`(10000). migration·kafka-init Job은 `mosemo-consumer`를 쓴다. batch Deployment가 가장 먼저 밀려난다.
- LimitRange(`limit-range.yaml`): requests/limits를 빠뜨린 컨테이너에 100m·128Mi / 1 CPU·512Mi를
  기본으로 넣고, 컨테이너 상한을 1 CPU·768Mi로 둔다.
- vpc-cni prefix delegation으로 노드 Pod 상한이 11에서 110으로 늘어난다(session-up.sh가 확인).
- API·consumer 업데이트는 아직 maxSurge=0, maxUnavailable=1이며 짧은 중단을 허용한다(G2에서 변경).

Kafka Pod에는 NetworkPolicy(`network-policies.yaml`, VPC CNI 적용)가 걸린다. 9092는 `app` 라벨이
mosemo-server·mosemo-consumer·mosemo-batch·mosemo-kafka-init·mosemo-kafka인 Pod만, 9093(KRaft
controller)은 mosemo-kafka만 들어올 수 있다. session-up.sh가 StatefulSet보다 먼저 적용한다. API에는
정책을 두지 않는다: 노드와 Pod가 같은 subnet이라 VPC CIDR 허용은 모든 Pod 허용과 같고, VPC CNI는
Service 포트와 컨테이너 포트가 같아야 하는데 API Service는 80→8000이다. egress는 제한하지 않는다.

Kafka Service는 ClusterIP이며 외부 포트를 열지 않는다. 애플리케이션 주소는
`mosemo-kafka.mosemo.svc.cluster.local:9092`다. controller의 자기 quorum 연결을 위해
publishNotReadyAddresses를 사용하며, 실제 배포 시 broker rollout과 topic Job 성공을
먼저 기다린다. PLAINTEXT·replication 1은 격리된 실습용이고 운영 구성이 아니다.

Kafka는 `data-mosemo-kafka-0` PVC를 사용하는 StatefulSet이다. 암호화 gp3 5GiB와
StorageClass `mosemo-kafka-gp3`(reclaimPolicy Retain, `WaitForFirstConsumer`)를 사용한다.
EBS CSI Driver·Pod Identity Agent·전용 controller IAM 역할은 Terraform으로 준비한다. 일반 node
역할에 EBS 권한은 붙이지 않는다. CSI controller는 2 replica와 PDB로 운영하고 snapshot sidecar는 끈다.
CSI node plugin은 Kafka 노드의 taint를 견디도록 설정한다.

kafka 그룹은 ap-southeast-2a로 고정해 교체 노드도 같은 AZ에 뜬다. 세션 안에서 Pod를 교체하면
같은 PVC로 메시지·offset을 복구한다(PVC retention Retain). EBS는 다른 AZ에 바로 붙일 수 없으며,
노드·AZ 장애 동안은 서비스가 중단된다. 복제·고가용성·백업은 제공하지 않는다. 로그 경로는
ext4 lost+found와 겹치지 않는 `/var/lib/kafka/data/kafka`다.

세션 간 데이터는 보존하지 않는다. RDS가 세션마다 새로 만들어지므로 Kafka만 이전 메시지·offset을
들고 오면 상태가 어긋난다. session-down.sh는 cluster destroy 전에 StatefulSet·PVC를 지우고,
Retain으로 남은 PV의 EBS volume ID(미리 읽은 값과 PVC 태그로 찾은 값)를 명시적으로 삭제·확인한 뒤
PV 객체를 지운다.
CSI가 붙인 `kubernetes.io/created-for/pvc/namespace=mosemo` 태그의 볼륨이 남아 있으면 destroy하지
않고 멈춘다. 다음 세션은 새 PVC로 빈 볼륨을 만든다. 이전 볼륨 재연결이나 복원 리허설은 필요할 때
별도 절차로 다룬다.

## 로컬 검증

```sh
bash infra/deployment/check-local.sh
```

Docker에서 linux/amd64 이미지를 빌드하고 임시 PostgreSQL 18.6·Kafka·API·별도 consumer를
실행한다. migration·재실행·head, 앱 DB 쿼리, consumer group Stable, OpenAPI HTTP 200,
비인증 API 401, 비루트 실행·정상 종료를 확인한다. 테스트 종료 시 생성한 컨테이너·볼륨·
네트워크·환경 파일을 정리하며 기존 Docker 리소스를 건드리지 않는다.
Kafka·API·consumer에 manifest와 같은 메모리 limit을 적용한다.
이 smoke는 메시지→DB 흐름이나 Kubernetes 스케줄링·RDS TLS·실제 OAuth를 검증하지 않는다.
Kafka→DB 흐름은 전체 integration tests로 검증한다.

동일 데이터 볼륨을 붙인 broker 컨테이너 교체 검증:

```sh
bash infra/deployment/check-kafka-persistence.sh
```

위 smoke에서 만든 amd64 이미지를 사용한다. 임시 named volume에 topic·메시지 2개와
consumer offset을 기록하고 broker 컨테이너를 삭제·재생성한 뒤 남은 메시지에서
이어 읽는지 확인한다. 생성한 테스트 볼륨은 종료 시 삭제한다. 로컬 파일 보존 검증이며
AWS EBS/CSI attach·EKS Pod 교체·전원 장애 검증은 아니다.

전체 검사와 Swift 실제 클라이언트의 로컬 서버 연동 검증:

```sh
bash .codex/skills/verification/scripts/validate.sh
uv run --locked python scripts/verify_focus_session_client.py --client-root /absolute/path/to/mosemo-macos
```

검증 환경은 fake 설정을 사용하는 별도 프로세스로 주입한다. 실제 `.env`를 테스트에
로드하지 않는다. Swift 검증은 임시 DB·Kafka와 테스트 토큰을 사용해 활동 저장과
집중 세션 연계를 확인한다. 실제 Kakao 로그인·AWS API 연동의 증거로 해석하지 않는다.

## 작업 세션 절차

운영과 같은 설정을 유지하고, 비용 때문에 필요할 때만 띄운다. 껐다 켜기 위한 처리는 아래 스크립트에만
둔다. 각 스크립트는 단계마다 검증하고 실패하면 즉시 멈춘다. 비밀값은 출력하지 않는다.

준비물:

- foundation 스택 적용(ECR·검증 완료 ACM 인증서), admin 권한 적용.
- AWS profile `mosemo-infra` 로그인, `CLOUDFLARE_API_TOKEN`(danuh.com Zone:DNS:Edit).
- 저장소 밖 세션 디렉터리 `$MOSEMO_SESSION_DIR`(기본 `~/.mosemo/lab`, 0700)와 그 안의
  `secrets.env`(0600). `KEY=value` 형식으로 `DB_PASSWORD`·`AUTH_JWT_SECRET_KEY`·`KAKAO_REST_API_KEY`·
  `KAKAO_CLIENT_SECRET`·`GOOGLE_API_KEY`만 둔다. 원본 .env 전체를 복사하지 않는다. DB_PASSWORD는 URL 안전 문자로 만들고
  RDS(TF_VAR_db_password)와 앱 Secret에 같은 값으로 쓰인다. 전용 kubeconfig도 이 디렉터리에 만든다.

```sh
bash infra/deployment/session-up.sh    # 세션 시작 (--profile measurement: 5단계 측정 세션)
bash infra/deployment/deploy.sh        # 현재 커밋 배포 (반복 가능)
bash infra/deployment/session-down.sh  # 세션 종료
bash infra/deployment/session-audit.sh # 남은 리소스 0개 확인
```

1. session-up.sh: 프로필 기록(`$MOSEMO_SESSION_DIR/profile`) → 세션 스택 `terraform apply`(plan을 검토하고
   yes 입력; Cloudflare API CNAME 포함) → 같은 환경의 `terraform plan -detailed-exitcode`가 0인지 확인
   → 전용 kubeconfig → 노드 Ready·prefix delegation·노드별 Pod 상한·allocatable memory(app 노드 1236Mi 미만이면
   "allocatable memory too low"로 멈춤)·CSI node plugin 확인
   → namespace·PriorityClass·LimitRange → 새 RDS 출력으로 렌더링한 ConfigMap → Secret(server-side apply)
   → 시드니 RDS CA ConfigMap → Kafka NetworkPolicy → StorageClass·StatefulSet rollout·PVC Bound·kafka 노드 배치 확인
   → 프로필별 목표 파티션(일반 4, 측정 8)으로 렌더한 topic 초기화 Job 성공.
2. deploy.sh: 깨끗한 작업 트리의 커밋 SHA 태그로 ECR 이미지를 찾고, 없을 때만 linux/amd64로 빌드·push한다
   (ECR 인증은 --password-stdin). 같은 `repository@sha256` digest로 migration Job 성공을 확인한 뒤
   API·consumer·batch를 rollout하고, ALB에 직접 연결해 HTTPS `/openapi.json` 200·비인증 `/api/v1/accounts/me` 401·
   HTTP 301, Cloudflare CNAME, consumer group Stable, batch label-proposals 성공 로그를 확인한다. 측정 프로필이면 batch 렌더·rollout·로그 확인을
   건너뛰고 남은 `mosemo-batch` Deployment를 지운다. 또 API·consumer를 replicas 4와 hostname
   topologySpreadConstraints(maxSkew 1, DoNotSchedule)로 렌더하고, rollout 뒤 app 노드마다 API 1·consumer 1인지 확인한다. 대화형 단계가 없어 G2 CI가 재사용할 수 있다.
3. session-down.sh: 최종 스냅샷 `mosemo-lab-final`이 이미 있으면 알리고 아무것도 바꾸지 않고 멈춤
   → Kafka StatefulSet·PVC 삭제 → Retain PV의 EBS를 volume ID로 삭제·확인 → PV 삭제
   → `-var skip_final_snapshot=true` plan이 RDS update 하나뿐인지 확인 후 apply
   (재실행이면 `enable_deletion_protection`만 바뀌는 ALB update도 허용) → RDS 삭제 보호 해제·available 대기
   → ALB 삭제 보호 해제·조회로 false 확인 → 같은 변수로 세션 스택 destroy → state 비었는지 확인
   → `profile`·`measurement.kubeconfig` 삭제(`measurement/` 결과는 남김).
   foundation은 건드리지 않는다. session-up이 apply 이후 단계에서 실패한 세션도 kubeconfig를 다시 만들어
   정리한다. apply 자체가 중간에 실패했다면 session-up.sh를 apply 성공까지 다시 실행한 뒤 정리한다.
   destroy가 실패하면 RDS·ALB 삭제 보호가 꺼져 있을 수 있으므로 다시 실행하거나 apply로 복구한다.
4. session-audit.sh: state 비어 있음, 시드니의 EKS 클러스터·랩 인스턴스·RDS 인스턴스·RDS 수동 스냅샷·LB·
   타깃 그룹·Kafka PVC EBS·미연결 EBS·EIP·랩 VPC·미연결 ENI가 0개인지 확인한다. 측정 창(`measurement/window`)이
   있으면 그 구간에 MosemoInfrastructureRole·AccountFullAccessRole 세션의 변경 이벤트(CloudTrail
   `ReadOnly=false`)가 0건인지 본다. 보존된 RDS 자동 백업은 경고만 한다. 남은 것이 있으면 exit 1.

`$MOSEMO_SESSION_DIR/measurement.lock`이 있으면 session-up·deploy·session-down·measurement-ready는
AWS·kubectl·terraform을 호출하기 전에 멈춘다.

렌더링 결과는 `$MOSEMO_SESSION_DIR/rendered`에 둔다. 이미 존재하는 Job은 실행 중이 아님을 확인한
뒤에만 삭제·재실행한다. Secret과 RDS CA는 서버 측 apply로 등록해 client-side last-applied
annotation에 복제하지 않는다. 디렉터리 전체를 일괄 apply하지 않는다.
인증된 활동 발행 202 후 consumer의 DB 처리와 실제 Kakao 로그인은 스크립트가 검증하지 않는다.

배포 뒤 consumer 확인: 배치의 건너뜀과 중단은 Pod 재시작 같은 신호 없이 로그로만 드러난다. 배포 뒤
다음 명령으로 `outcome`이 `skipped`·`failed`인 줄을 확인한다.

```sh
kubectl -n mosemo logs deploy/mosemo-consumer | grep -E '"outcome":"(skipped|failed)"'
```

- ERROR 수준의 `"outcome":"skipped"` 줄은 분류에 없는 예외, 곧 코드 버그다. 버그를 고쳐 배포한 뒤
  Consumer group의 offset을 그 줄의 `topic`·`partition`·`offset`으로 되돌려 다시 처리한다. receipt가
  이미 처리한 배치를 걸러 중복이 생기지 않으며, 다시 읽을 수 있는 기간은 토픽 보관 기간까지다.
- WARNING 수준의 `skipped`는 형식 오류·영구 실패라 다시 처리해도 같은 결과다.
- `failed`는 일시 실패의 재시도를 다 써서 consumer가 끝나기 직전의 위치다. offset을 확정하지 않았으므로
  장애가 풀리면 재시작한 consumer가 그 위치부터 이어서 처리한다.
- 로그는 Pod 안에만 있어 Pod가 교체되면 사라진다. 배포·재시작 전에 필요한 줄을 확인해 둔다.

## 5단계 측정 세션

측정 프로필(`session-up.sh --profile measurement`)은 loadgen 노드 그룹(1대, 2a, taint
`dedicated=loadgen:NoSchedule`, label `pool=loadgen`)을 더하고 batch를 뺀다. 모든 노드는 크레딧 없는
`m7i.large`이고 2a에 있으며, app 그룹은 4대 고정(kafka·loadgen 각 1대)이다. 토픽 파티션은 8개다
(consumer 4 Pod × max_workers 2 = 그룹 멤버 8과 1:1). session-up.sh가 kafka-init Job을 렌더할 때 넣는다.
API·consumer는 각 4 replica로 app 노드마다 1개씩 둔다(deploy.sh가 렌더·배치 확인). 프로필은 tfvars가 아니라
세션 디렉터리의 `profile` 기록으로 정해지며 `tf()`가 모든 terraform 호출에 `TF_VAR_measurement`를 붙인다.
플래그 없이 다시 실행하면 기록을 따른다. state가 남아 있는데 다른 프로필을 요청하면 실패한다.
측정 프로필로 새로 시작할 때 이전 `measurement/`는 `measurement.<UTC>/`로 옮긴다.

| 스크립트 | 하는 일 | 결과 (`$MOSEMO_SESSION_DIR/measurement/`) |
| --- | --- | --- |
| session-up.sh --profile measurement | apply 직후 plan exit code와 commit·dirty_files 기록, 측정 운영자 RBAC 적용 | terraform.json |
| measurement-ready.sh EXPECTED_ALEMBIC_HEAD | P1~P9 점검(측정용 권한 매트릭스, 이미지 digest·커밋·alembic head, RDS 설정·5432, API HTTP, postgres:18.3 pull, loadgen 배치·여유, 앱 상태, 크레딧 시작값, EKS 애드온 목록). 노드 그룹 크레딧은 T 계열 인스턴스의 실제 값, 그 밖은 `none` | ready.txt, manifest.json, manifest.sha256 |
| measurement-lock.sh start POD EXPECTED_END_UTC | 측정 Pod 생성 **전에** 실행. 잠금 기록(loadgen 노드 이름 포함), 예상 끝 + 30분(1~13시간) 토큰 kubeconfig 발급 | `../measurement.lock`, `../measurement.kubeconfig` |
| measurement-observe.sh start | 잠금 start 뒤. kafka 노드에 lab-dbstat Pod(30초마다 pg_stat_database), 백그라운드 샘플러(30초마다 API·consumer Pod별 cgroup cpu.stat·memory.current). 잠금이 없으면 거절. 샘플러는 잠금이 사라지면 스스로 멈춘다 | observe/cpu-stat.csv, observe/sampler.pid, observe/observe.log |
| measurement-lock.sh end [--invalid 사유] | 잠금 해제. `--invalid`가 없으면 이전 invalid를 지운다 | window, invalid |
| measurement-observe.sh stop | 잠금 end 뒤(window가 없거나 잠금이 있으면 거절). 샘플러 종료, lab-dbstat 로그 저장 후 Pod 삭제, 측정 창 RDS 1분 지표(DatabaseConnections 평균·최대, CPUUtilization, Read/WriteIOPS, DiskQueueDepth, WriteLatency, FreeableMemory) | observe/pg-stat-database.csv, observe/rds-extra.csv |
| measurement-metrics.sh | 측정 창의 CloudWatch 지표 CSV. **session-down 전에** 실행 | metrics.csv |
| session-audit.sh | 남은 리소스 0개, 측정 창 CloudTrail 변경 0건 | `../audit.txt` |
| session-cost.sh YYYY-MM-DD | 다음 날 Cost Explorer 1회($0.01), 서비스 × 기록 유형, 사용·크레딧 합계 | 화면 출력 |

측정 운영자 권한(`measurement-rbac.yaml`, ServiceAccount `step5-operator`, 네임스페이스 mosemo):
Pod 생성·삭제·조회, exec, logs, Deployment 조회, `mosemo-consumer`의 scale만. 기대 매트릭스는
`measurement-rbac-matrix.txt`이며 measurement-ready.sh P1이 10분짜리 토큰으로 확인한다.
RBAC는 Pod 생성을 이름으로 제한할 수 없어 다른 Pod도 만들 수 있다.

리허설(첫 기동, 사용자 허락 후):

1. `session-up.sh --profile measurement` → `deploy.sh` → `measurement-ready.sh <alembic head>`
2. `measurement-lock.sh start step5-tools <UTC 끝>` → `measurement-observe.sh start`
3. 측정 쪽 점검 약 15분: `measurement.kubeconfig`로 step5-tools Pod 생성 → `kubectl cp` 소형 덤프 →
   RDS `pg_restore --clean` → API HTTP 1회 → Pod 삭제
4. `measurement-lock.sh end --invalid "rehearsal"` → `measurement-observe.sh stop` → `measurement-metrics.sh` →
   `session-down.sh` → `session-audit.sh`

측정:

1. `session-up.sh --profile measurement` → `deploy.sh` → `measurement-ready.sh <alembic head>`
   (ready.txt·manifest.json·manifest.sha256을 측정 쪽에 전달)
2. `measurement-lock.sh start step5-tools <UTC 끝>` → `measurement-observe.sh start` → 측정 →
   측정 쪽 "측정 끝" 신호 → `measurement-lock.sh end`
3. `measurement-observe.sh stop` → `measurement-metrics.sh` → `session-down.sh` → `session-audit.sh` → 다음 날 `session-cost.sh <측정일>`

12시간을 넘겨도 "측정 끝" 신호가 없으면: 측정 Pod 삭제 → `measurement-lock.sh end --invalid "<사유>"` →
`measurement-observe.sh stop` → 사용자에게 알린다. 랩 내리기(session-down)는 사용자 지시가 있을 때만 한다.

측정 쪽에 알릴 차이:

- kubeconfig는 `$MOSEMO_SESSION_DIR/measurement.kubeconfig`(예상 끝 + 30분, 최소 1시간·최대 13시간 토큰, `measurement-lock.sh start`가 발급).
- 측정 Pod에 toleration `dedicated=loadgen:NoSchedule`, nodeSelector `pool: loadgen`,
  `activeDeadlineSeconds: 43200`이 필요하다.
- 측정 세션에는 batch가 없다. scale 대상은 `mosemo-consumer`뿐이며, 0으로 줄였다면 4로 되돌린다.
- 노드는 m7i.large(버스트 없음)라 EC2 CPU 크레딧 지표는 없다. EC2 CPUUtilization과 RDS 지표는 1분 해상도다.

## 오프라인 검증

```sh
bash infra/deployment/tests/run.sh         # 가짜 aws·kubectl·terraform·docker(·git·curl·dig)로 스크립트 분기 검증
bash infra/deployment/tests/observe.sh      # 가짜 kubectl·aws로 measurement-observe.sh 거절 조건과 start/stop 결과 검증
bash infra/deployment/tests/kind-check.sh   # 일회용 kind 클러스터: 매니페스트(측정 프로필 렌더 포함) server dry-run, 측정 RBAC 매트릭스
```

run.sh는 호출을 기록만 하는 가짜 명령을 PATH 앞에 두고 잠금 거절(호출 0건), 측정 프로필 전달,
batch 제외, 프로필별 API·consumer 렌더(replicas·spread)와 측정 배치 확인, session-down 재실행 가드, 잠금 start/end(토큰 기간), 프로필 유지, measurement-ready 매니페스트(애드온·크레딧·AZ·인스턴스 타입)를 확인한다. AWS·클러스터 동작의 증거는 아니다.

## 실제 인증과 HTTPS

실제 키는 보호된 배포 Secret 파일로 준비했어도, apply하지 않으면 실행 중 설정을
교체한 것이 아니다. Kakao Developer 등록과 실제 로그인은 별도 확인이 필요하다.
macOS 앱의 API 주소는 빌드 설정 `MOSEMO_API_BASE_URL`로 주입할 수 있어 소스 수정은 필요 없다.

공개 HTTPS용 ConfigMap은 `MOSEMO_ENV=prod`를 사용한다. 현재 서버는 이 값일 때
OAuth state·PKCE 쿠키에 Secure를 붙인다. AWS 리소스 태그 Environment=lab과는 별개이며
노드 규모·비용·리소스 수명 설정은 바꾸지 않는다.

확정된 API 호스트는 mosemo-api.danuh.com이다. 향후 배포 시 사용자가 Kakao에 등록할 서버 Redirect URI는 다음이다.

```text
https://mosemo-api.danuh.com/api/v1/auth/kakao/callback
```

같은 URL을 서버 KAKAO_REDIRECT_URI에 넣고 앱 API base URL은 https://mosemo-api.danuh.com으로 맞춘다.
macOS custom callback `io.mosemo.app:/auth/callback`은 서버→앱 복귀용이다.
DNS는 Cloudflare에 유지한다. ACM 검증 CNAME은 foundation, API CNAME은 세션 스택이 Terraform으로 관리한다.
공식 등록 방법: https://developers.kakao.com/docs/en/kakaologin/prerequisite
