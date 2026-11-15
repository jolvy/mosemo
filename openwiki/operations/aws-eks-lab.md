---
type: operations
title: AWS EKS 랩과 측정 세션
description: 운영 복제 AWS 환경(Terraform root 3개, EKS·RDS·ALB, Kubernetes manifest, Docker 이미지)과 작업 세션 스크립트(session-up·deploy·session-down·session-audit), 5단계 부하 측정 세션 절차를 정리한다.
tags: [operations, aws, eks, terraform, kubernetes, deployment, measurement]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-15T02:51:25.465Z
sources:
  - id: openwiki-source-7b59c8a3b46cd6f5a5872a42
    resource: repo://infra/deployment/README.md
  - id: openwiki-source-a399ef57993632bd3c3ad8b3
    resource: repo://infra/terraform/README.md
generated: { by: "claude-code", at: "2026-11-15T02:51:25.465Z" }
---

# AWS EKS 랩과 측정 세션

`infra/`는 운영과 같은 설정의 AWS 환경을 코드로 둔다. 비용 때문에 작업 세션마다 띄우고 내릴 뿐이며, 껐다 켜기 위한 처리는 설정이 아니라 `infra/deployment`의 세션 스크립트에만 둔다. 실제 apply·ECR push·Kubernetes 적용은 별도 승인 뒤에만 실행한다. 원본은 [infra/terraform/README.md](../../infra/terraform/README.md)와 [infra/deployment/README.md](../../infra/deployment/README.md)이며, 이 페이지와 다르면 원본이 맞다.

## Terraform 실행 단위

| Root | 수명 | 관리 대상 |
| --- | --- | --- |
| `admin/` | 장기 | IAM 실행 역할·권한(`policies/*.json`). 변경은 plan 검토·승인 뒤 적용 |
| `foundation/` | 장기 | ECR 저장소, ACM 인증서와 Cloudflare 검증 CNAME, ALB 접근 로그 S3 버킷(90일 수명 주기) |
| `infrastructure/` | 작업 세션 | VPC·EKS·노드 그룹·add-on·ALB·HTTPS 리스너·RDS·API CNAME |

- 세 root는 S3 state 버킷을 공유하고 key와 lock을 따로 쓴다. 계정 ID·Cloudflare zone ID는 커밋하지 않고, 각 root의 `backend.hcl.example`·`terraform.tfvars.example`을 Git 제외 파일로 복사해 `init -backend-config=backend.hcl`로 초기화한다. 상위 디렉터리에서 Terraform을 실행하지 않는다.
- `infrastructure`는 foundation의 `ecr_repository_url`·`api_certificate_arn`·`alb_access_log_bucket` 출력만 참조하므로 foundation이 먼저 적용되어 있어야 한다.
- DB 비밀번호(`TF_VAR_db_password`)는 세션 스크립트가 저장소 밖 비밀값 파일에서 읽어 프로세스 안에서만 넘긴다.
- 각 root에는 `*.tftest.hcl`이 있어 `terraform test`로 검증한다.

## 이미지와 프로세스

[Dockerfile](../../Dockerfile)은 Python 3.14·uv로 locked 설치하고 UID/GID 10001로 실행한다. `.dockerignore`는 소스·migration·빌드 입력만 허용한다. 모든 프로세스는 **같은 이미지 digest**를 쓴다.

| 프로세스 | manifest(`infra/kubernetes/`) | 실행 |
| --- | --- | --- |
| API | `deployment.yaml` | uvicorn `mosemo.entrypoints.api.main:app`, port 8000. probe는 `/openapi.json` |
| 활동 consumer | `consumer-deployment.yaml` | `python -m mosemo.entrypoints.consumer`. HTTP Service·probe 없음. 실패하면 종료하고 Kubernetes가 재시작, 종료 유예 45초 > drain 30초 |
| 배치 job | `batch-deployment.yaml` | `python -m mosemo.entrypoints.batch serve`. 라벨 제안 job 하나, replicas=1·maxSurge=0으로 중복 실행 방지 |
| 마이그레이션 | `migration-job.yaml` | `alembic upgrade head` |
| 랩 Kafka | `kafka.yaml` | StatefulSet 1개, KRaft, PLAINTEXT·replication 1(격리된 실습용) |
| 토픽 초기화 | `kafka-init-job.yaml` | 목표 파티션까지 늘리고 줄이지 않음 |

- 노드는 기본 t3.small. Kafka 전용 그룹 1대(taint `dedicated=kafka:NoSchedule`, ap-southeast-2a 고정)와 나머지가 함께 쓰는 app 그룹 1~2대다.
- PriorityClass는 `mosemo-api` > `mosemo-consumer` > `mosemo-batch` 순이라 batch가 가장 먼저 밀려난다. LimitRange가 requests/limits 기본값과 컨테이너 상한을 넣는다.
- Kafka의 9092는 앱 Pod와 kafka-init만, 9093(KRaft controller)은 Kafka 자신만 들어오도록 NetworkPolicy가 막는다. Kafka 데이터는 암호화 gp3 PVC(StorageClass `mosemo-kafka-gp3`, Retain)에 둔다.
- 세션 사이 데이터는 보존하지 않는다. RDS가 세션마다 새로 만들어지므로 Kafka만 옛 메시지·offset을 들고 오면 상태가 어긋나기 때문이다.

## 작업 세션 절차

```sh
bash infra/deployment/session-up.sh    # 세션 시작 (--profile measurement: 5단계 측정 세션)
bash infra/deployment/deploy.sh        # 현재 커밋 배포 (반복 가능)
bash infra/deployment/session-down.sh  # 세션 종료
bash infra/deployment/session-audit.sh # 남은 리소스 0개 확인
```

비밀값과 전용 kubeconfig는 저장소 밖 `$MOSEMO_SESSION_DIR`(기본 `~/.mosemo/lab`)에 둔다. 각 스크립트는 단계마다 검증하고 실패하면 즉시 멈추며 비밀값을 출력하지 않는다.

1. **session-up.sh**: 세션 스택 apply(plan 검토 후 yes) → 다시 plan해 변경 없음 확인 → 노드 Ready·prefix delegation·allocatable memory·CSI 확인 → namespace·PriorityClass·LimitRange·ConfigMap·Secret(server-side apply)·RDS CA·NetworkPolicy → Kafka StatefulSet·PVC → 토픽 초기화 Job 성공.
2. **deploy.sh**: 깨끗한 작업 트리의 커밋 SHA 태그로 ECR 이미지를 찾고 없을 때만 linux/amd64로 빌드·push한다. 같은 digest로 migration Job 성공을 확인한 뒤 API·consumer·batch를 rollout하고, HTTPS `/openapi.json` 200·비인증 `/api/v1/accounts/me` 401·HTTP 301, CNAME, consumer group Stable, batch 성공 로그를 확인한다.
3. **session-down.sh**: Kafka StatefulSet·PVC와 Retain EBS를 지우고, RDS 최종 스냅샷을 건너뛰도록 apply한 뒤 RDS·ALB 삭제 보호를 풀고 세션 스택을 destroy한다. foundation은 건드리지 않는다.
4. **session-audit.sh**: state가 비었고 EKS·인스턴스·RDS·LB·EBS·EIP·VPC·ENI가 0개인지, 측정 창이 있으면 그 구간 CloudTrail 변경 이벤트가 0건인지 확인한다. 남은 것이 있으면 exit 1.

`$MOSEMO_SESSION_DIR/measurement.lock`이 있으면 session-up·deploy·session-down·measurement-ready는 AWS·kubectl·terraform을 호출하기 전에 멈춘다.

## 5단계 측정 세션

`session-up.sh --profile measurement`는 loadgen 노드 그룹(1대, taint `dedicated=loadgen:NoSchedule`)을 더하고 batch를 뺀다. 모든 노드는 크레딧 없는 `m7i.large`, app 그룹은 4대 고정, 토픽 파티션은 8개(consumer 4 Pod × max_workers 2)다. deploy.sh는 API·consumer를 4 replica로 app 노드마다 하나씩 둔다. 프로필은 세션 디렉터리의 `profile` 기록으로 정해지고, state가 남았는데 다른 프로필을 요청하면 실패한다.

| 스크립트 | 하는 일 |
| --- | --- |
| `measurement-ready.sh` | 권한 매트릭스, 이미지 digest·커밋·alembic head, RDS·API·loadgen·크레딧 등 P1~P9 사전 점검 |
| `measurement-lock.sh start/end` | 측정 창 잠금과 기간 한정 토큰 kubeconfig 발급·해제 |
| `measurement-observe.sh start/stop` | `lab-dbstat` Pod로 `pg_stat_database`, API·consumer cgroup CPU·메모리, RDS 1분 지표 수집 |
| `measurement-metrics.sh` | 측정 창 CloudWatch 지표 CSV(session-down 전에 실행) |
| `session-cost.sh` | 다음 날 Cost Explorer 비용 조회 |

측정 운영자 권한은 `measurement-rbac.yaml`의 ServiceAccount `step5-operator`(Pod 생성·삭제·exec·logs, consumer scale)이며 기대 매트릭스는 `measurement-rbac-matrix.txt`다.

## 로컬 검증

- `bash infra/deployment/check-local.sh`: linux/amd64 이미지로 임시 PostgreSQL·Kafka·API·consumer를 띄워 migration, consumer group Stable, OpenAPI 200, 비인증 401, 비루트 실행을 확인한다. 메시지→DB 흐름이나 Kubernetes·RDS·OAuth는 검증하지 않는다.
- `bash infra/deployment/check-kafka-persistence.sh`: 같은 볼륨으로 broker 컨테이너를 교체해 메시지·offset이 이어지는지 확인한다.
- `infra/deployment/tests/`에는 kind 기반 검사(`kind-check.sh`, `observe.sh`, `run.sh`)가 있다.
- 무중단 배포 자동화 비교와 제안은 [AUTOMATION.md](../../infra/deployment/AUTOMATION.md)에 있다. 현재 API·consumer 업데이트는 maxSurge=0, maxUnavailable=1로 짧은 중단을 허용한다.

## 관련 페이지

- [영속성·메시징·배치 실행](../architecture/persistence-and-messaging.md)
- [빠른 시작](../quickstart.md)
