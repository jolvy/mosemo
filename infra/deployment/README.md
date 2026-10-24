# AWS 수동 배포 준비와 검증

2026-10-22 작업은 기존 AWS 실습 리소스를 정리하고, origin/main의 Kafka 연동에
맞춘 설정을 **plan까지** 검증하는 범위다. 아래 배포 명령은 향후 별도 배포 요청을
받았을 때의 절차다. 이번에는 재생성 apply·ECR push·Kubernetes 실제 적용을 하지 않는다.

## 이미지와 프로세스

Dockerfile은 Python 3.14·uv 0.11.21로 locked 설치하고 UID/GID 10001로 실행한다.
`.dockerignore`의 허용 목록에는 소스·migration·빌드 입력만 포함된다. 환경 파일,
Terraform state·plan, 로컬 handoff·Secret은 이미지에 복사하지 않는다.

| 프로세스 | manifest | 실행 |
| --- | --- | --- |
| API | deployment.yaml | uvicorn mosemo.main:app, port 8000 |
| Activity consumer | consumer-deployment.yaml | python -m mosemo.activities.consumer |
| Migration | migration-job.yaml | alembic upgrade head |
| Lab Kafka | kafka.yaml | StatefulSet 1개, cp-kafka 7.8.0, KRaft broker/controller |
| Topic 초기화 | kafka-init-job.yaml | 2 partitions, replication factor 1 |

API·consumer·migration은 **같은 새 이미지 digest**를 사용한다. 기존 삭제한 ECR
이미지의 digest는 재사용할 수 없다. label proposal worker와 별도 batch worker는
추가하지 않는다. activity consumer만 이번 요청의 처리 프로세스로 준비한다.

API와 consumer는 같은 DB·BATCH_TOPIC·BATCH_GROUP_ID·Kafka 주소를 사용한다.
consumer에는 HTTP Service·가짜 HTTP probe를 붙이지 않는다. consumer Pod Running은
구독·DB 처리 성공의 증거가 아니므로 실제 메시지→DB 저장과 group/offset을 확인한다.
consumer는 실패 시 프로세스를 종료하고 Kubernetes가 재시작한다. 종료 유예 45초는
consumer의 30초 drain보다 길게 두었다. API probes는 /openapi.json만 확인한다.

## 단일 노드 실습 제한

사용자 지정 `t3.small` 1대를 유지한다. API·consumer는 각각 224Mi,
Kafka는 512Mi를 요청한다. Kafka heap max 256Mi, log cleaner dedupe buffer 32Mi,
container limit 768Mi,
topic Job은 request 128Mi/limit 256Mi다. API·consumer 업데이트는 maxSurge=0,
maxUnavailable=1로 여분 Pod를 만들지 않으며 짧은 중단을 허용한다. 작은 broker에 대한
부하 성능과 EKS 스케줄링은 미검증이다.

Kafka Service는 ClusterIP이며 외부 포트를 열지 않는다. 애플리케이션 주소는
`mosemo-kafka.mosemo.svc.cluster.local:9092`다. controller의 자기 quorum 연결을 위해
publishNotReadyAddresses를 사용하며, 실제 배포 시 broker rollout과 topic Job 성공을
먼저 기다린다. PLAINTEXT·replication 1은 격리된 실습용이고 운영 구성이 아니다.

Kafka는 `data-mosemo-kafka-0` PVC를 사용하는 StatefulSet이다. 암호화 gp3 5GiB와
StorageClass `mosemo-kafka-gp3`를 사용한다. EBS CSI Driver·Pod Identity Agent·전용
controller IAM 역할은 Terraform으로 준비한다. 일반 node 역할에 EBS 권한은 붙이지 않는다.
CSI controller는 실습용 1 replica이며 snapshot sidecar·PDB는 끈다. CSI 시스템 Pod도
노드 메모리·Pod 수를 사용하므로 최종 EKS 스케줄링은 실제 배포 시 확인한다.

StorageClass는 `WaitForFirstConsumer`로 처음 스케줄되는 노드의 AZ에 볼륨을 만든다.
node group은 ap-southeast-2a로 고정해 교체 노드도 같은 AZ에 뜨도록 한다.
동일 이름 StatefulSet의 Pod를 교체하면 같은 PVC로 메시지·offset을 복구할 수 있다.
EBS는 다른 AZ에 바로 붙일 수 없으며, 노드·AZ 장애 동안은 서비스가 중단된다.
이 구성은 복제·고가용성·백업을 제공하지 않는다. 갑작스러운 장애의 모든 데이터 보존을
보장하지 않는다. 로그 경로는 ext4 lost+found와 겹치지 않는 `/var/lib/kafka/data/kafka`다.

StatefulSet 삭제·scale-down 시 PVC를 보존하고, PVC 삭제 시에도 PV/EBS는 `Retain`한다.
다시 사용할 때 StatefulSet/PVC 이름을 유지한다. PVC를 삭제했다면 기존 PV는 Released
상태일 수 있어 자동으로 새 PVC에 붙지 않는다. volume ID·AZ·claim을 확인한 뒤 기존
PV를 재바인딩해야 하며, 빈 볼륨으로 바꿔 복구 성공으로 처리하지 않는다.
cluster를 destroy해도 이 Kubernetes 생성 EBS는 Terraform state 밖에 남을 수 있고
저장 비용이 계속 발생한다. 정리 전 PV·PVC·volume ID·AZ를 저장하고 보존/삭제를
결정한 뒤 해당 디스크만 명시적으로 처리한다. 디스크 손상·삭제는 별도 백업 없이는
복구를 보장하지 않는다. 보존 기간과 디스크 사용량을 관찰해야 한다.
cluster를 새로 만들면 기존 Kubernetes PV/PVC 객체도 사라져 있다. 남겨둔 EBS가
자동 검색·연결되지 않으므로, 저장한 volume ID로 같은 AZ에 static PV/PVC를 복원한 뒤
StatefulSet을 시작해야 한다. 새 동적 PVC로 빈 디스크를 만들지 않도록 주의한다.

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

## 향후 배포 순서 — 이번 실행에서 적용 금지

1. 별도 요청으로 admin IAM의 EBS CSI 역할·추가 권한을 먼저 적용하고,
   infrastructure를 재생성한 후 STS 계정·전용 kubeconfig를 확인한다.
   기본 ~/.kube/config를 덮어쓰지 않는다. 새 database 출력과 api_url을 읽는다.
2. 새 linux/amd64 이미지 빌드·ECR push 후 repository@sha256 digest를 조회한다.
   ECR 인증은 --password-stdin을 사용하고 인증 토큰을 출력하거나 imagePullSecrets에 복사하지 않는다.
3. ConfigMap의 DB_HOST·KAKAO_REDIRECT_URI를 **새 출력값**으로 렌더링한다.
   실제 macOS callback은 `io.mosemo.app:/auth/callback`이다.
4. 저장소 밖 0700 디렉터리에 실제 DB 비밀번호·JWT·Kakao 키를 선택해 Secret을 준비한다.
   원본 .env 전체를 복사하지 않는다. Secret 파일은 0600으로 두고 값·base64·DSN을 출력하지 않는다.
5. namespace → 앱 ConfigMap·Secret → 시드니 RDS CA ConfigMap을 준비한다.
   RDS CA는 https://truststore.pki.rds.amazonaws.com/ap-southeast-2/ap-southeast-2-bundle.pem 을 사용한다.
   API·consumer·migration 모두 PGSSLMODE=verify-full과 CA mount를 사용한다.
6. Pod Identity Agent·EBS CSI add-on ACTIVE와 controller·node Pod 정상 상태를 확인한다.
   kafka-storage-class.yaml → Kafka headless Service·StatefulSet 적용 후
   `kubectl -n mosemo rollout status statefulset/mosemo-kafka`와 PVC Bound·PV의
   volume ID·AZ를 확인한다. Kafka topic 초기화 Job 성공을 확인한다.
7. 아래와 같이 동일 digest를 렌더링하고 migration Job만 먼저 생성해 성공을 확인한다.
8. API·consumer Deployment와 NodePort Service를 적용한다. 디렉터리 전체를 일괄 apply하지 않는다.
9. API rollout·consumer group·재시작·OOM·DB 쿼리·ALB target healthy·HTTP 응답을 검증한다.
   인증된 활동 발행은 202 후 consumer DB 처리까지 기다려 최종 조회로 검증한다.
   테스트 메시지·group offset을 기록한 뒤 Kafka Pod 교체 전후 동일 PVC/PV·메시지·offset을
   확인한다. 현재 실행 중 Kafka가 없어 이전 임시 디스크에서 이관할 데이터는 없다.

역할·추가 권한과 add-on은 각각 admin/ebs-csi.tf·infrastructure/storage.tf에 명시한다.
plan 성공은 미적용 새 역할/권한의 실제 EBS 생성·연결 성공을 증명하지 않는다.
이전 ephemeral Deployment/일반 ClusterIP Service가 남은 다른 환경이라면 먼저 중단·
데이터 이관을 결정해야 한다. 현재 AWS 환경은 이미 삭제돼 실제 전환을 수행하지 않는다.

이미지 렌더링 예시 (DEPLOY_DIR는 0700, DEPLOY_IMAGE는 새 digest):

```sh
kubectl set image --local -f infra/kubernetes/migration-job.yaml "migration=$DEPLOY_IMAGE" -o yaml > "$DEPLOY_DIR/migration-job.yaml"
kubectl set image --local -f infra/kubernetes/deployment.yaml "server=$DEPLOY_IMAGE" -o yaml > "$DEPLOY_DIR/deployment.yaml"
kubectl set image --local -f infra/kubernetes/consumer-deployment.yaml "consumer=$DEPLOY_IMAGE" -o yaml > "$DEPLOY_DIR/consumer-deployment.yaml"
```

이미 존재하는 Job은 실행 중이 아님을 확인한 뒤에만 삭제·재실행한다. Secret은
서버 측 apply로 등록해 client-side last-applied annotation에 복제하지 않는다.

## 실제 인증과 HTTPS

실제 키는 보호된 배포 Secret 파일로 준비했어도, apply하지 않으면 실행 중 설정을
교체한 것이 아니다. Kakao Developer 등록과 실제 로그인은 별도 확인이 필요하다.
macOS 앱의 API 주소는 빌드 설정 `MOSEMO_API_BASE_URL`로 주입할 수 있어 소스 수정은 필요 없다.

공개 HTTPS용 ConfigMap은 `MOSEMO_ENV=prod`를 사용한다. 현재 서버는 이 값일 때
OAuth state·PKCE 쿠키에 Secure를 붙인다. AWS 리소스 태그 Environment=lab과는 별개이며
노드 규모·비용·리소스 수명 설정은 바꾸지 않는다.

확정된 API 호스트는 mosemo-api.danuh.com이다. HTTPS 준비는 미적용 상태다.
향후 배포 시 사용자가 Kakao에 등록할 서버 Redirect URI는 다음이다.

```text
https://mosemo-api.danuh.com/api/v1/auth/kakao/callback
```

같은 URL을 서버 KAKAO_REDIRECT_URI에 넣고 앱 API base URL은 https://mosemo-api.danuh.com으로 맞춘다.
macOS custom callback `io.mosemo.app:/auth/callback`은 서버→앱 복귀용이다.
삭제한 ALB DNS는 더 이상 사용할 수 없다. DNS는 Cloudflare에 유지한다.
ACM 검증 CNAME·API CNAME은 별도 DNS 변경 승인 후 등록한다.
인증서 요청/검증/리스너의 단계는 [인프라 문서](../terraform/infrastructure/README.md)를 따른다.
공식 등록 방법: https://developers.kakao.com/docs/en/kakaologin/prerequisite
