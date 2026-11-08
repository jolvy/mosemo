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

## 노드 배치와 자원

노드는 t3.small 2대가 기본이다. Kafka 전용 그룹 1대(ap-southeast-2a, taint `dedicated=kafka:NoSchedule`)와
API·consumer·migration·시스템 add-on이 함께 쓰는 app 그룹 1~2대(2a·2b)다. Kafka StatefulSet만
`pool: kafka` nodeSelector와 toleration을 가진다. API·consumer는 각각 224Mi를 요청하고 limit 512Mi,
Kafka는 512Mi 요청·768Mi limit, heap max 256Mi, log cleaner dedupe buffer 32Mi다.
topic Job은 request 128Mi/limit 256Mi다. requests는 첫 세션 실측 후 재산정한다.

- PriorityClass(`priority-classes.yaml`): `mosemo-api`(30000) > `mosemo-consumer`(20000) >
  `mosemo-batch`(10000). batch 클래스는 정의만 하고 아직 쓰는 워크로드가 없다.
- LimitRange(`limit-range.yaml`): requests/limits를 빠뜨린 컨테이너에 100m·128Mi / 1 CPU·512Mi를
  기본으로 넣고, 컨테이너 상한을 1 CPU·768Mi로 둔다.
- vpc-cni prefix delegation으로 노드 Pod 상한이 11에서 110으로 늘어난다(session-up.sh가 확인).
- API·consumer 업데이트는 아직 maxSurge=0, maxUnavailable=1이며 짧은 중단을 허용한다(G2에서 변경).

Kafka Service는 ClusterIP이며 외부 포트를 열지 않는다. 애플리케이션 주소는
`mosemo-kafka.mosemo.svc.cluster.local:9092`다. controller의 자기 quorum 연결을 위해
publishNotReadyAddresses를 사용하며, 실제 배포 시 broker rollout과 topic Job 성공을
먼저 기다린다. PLAINTEXT·replication 1은 격리된 실습용이고 운영 구성이 아니다.

Kafka는 `data-mosemo-kafka-0` PVC를 사용하는 StatefulSet이다. 암호화 gp3 5GiB와
StorageClass `mosemo-kafka-gp3`(reclaimPolicy Retain, `WaitForFirstConsumer`)를 사용한다.
EBS CSI Driver·Pod Identity Agent·전용 controller IAM 역할은 Terraform으로 준비한다. 일반 node
역할에 EBS 권한은 붙이지 않는다. CSI controller는 1 replica이며 snapshot sidecar·PDB는 끈다.
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
  `KAKAO_CLIENT_SECRET`만 둔다. 원본 .env 전체를 복사하지 않는다. DB_PASSWORD는 URL 안전 문자로 만들고
  RDS(TF_VAR_db_password)와 앱 Secret에 같은 값으로 쓰인다. 전용 kubeconfig도 이 디렉터리에 만든다.

```sh
bash infra/deployment/session-up.sh    # 세션 시작
bash infra/deployment/deploy.sh        # 현재 커밋 배포 (반복 가능)
bash infra/deployment/session-down.sh  # 세션 종료
```

1. session-up.sh: 세션 스택 `terraform apply`(plan을 검토하고 yes 입력; Cloudflare API CNAME 포함)
   → 전용 kubeconfig → 노드 Ready·prefix delegation·노드별 Pod 상한·CSI node plugin 확인
   → namespace·PriorityClass·LimitRange → 새 RDS 출력으로 렌더링한 ConfigMap → Secret(server-side apply)
   → 시드니 RDS CA ConfigMap → Kafka StorageClass·StatefulSet rollout·PVC Bound·kafka 노드 배치 확인
   → topic 초기화 Job 성공.
2. deploy.sh: 깨끗한 작업 트리의 커밋 SHA 태그로 ECR 이미지를 찾고, 없을 때만 linux/amd64로 빌드·push한다
   (ECR 인증은 --password-stdin). 같은 `repository@sha256` digest로 migration Job 성공을 확인한 뒤
   API·consumer를 rollout하고, ALB에 직접 연결해 HTTPS `/openapi.json` 200·비인증 `/api/v1/accounts/me` 401·
   HTTP 301, Cloudflare CNAME, consumer group Stable을 확인한다. 대화형 단계가 없어 G2 CI가 재사용할 수 있다.
3. session-down.sh: Kafka StatefulSet·PVC 삭제 → Retain PV의 EBS를 volume ID로 삭제·확인 → PV 삭제
   → `-var skip_final_snapshot=true` plan이 RDS update 하나뿐인지 확인 후 apply
   → RDS 삭제 보호 해제·available 대기 → 같은 변수로 세션 스택 destroy → state 비었는지 확인.
   foundation은 건드리지 않는다. session-up이 apply 이후 단계에서 실패한 세션도 kubeconfig를 다시 만들어
   정리한다. apply 자체가 중간에 실패했다면 session-up.sh를 apply 성공까지 다시 실행한 뒤 정리한다.
   destroy가 실패하면 RDS 삭제 보호가 꺼져 있을 수 있으므로 다시 실행하거나 apply로 복구한다.

렌더링 결과는 `$MOSEMO_SESSION_DIR/rendered`에 둔다. 이미 존재하는 Job은 실행 중이 아님을 확인한
뒤에만 삭제·재실행한다. Secret과 RDS CA는 서버 측 apply로 등록해 client-side last-applied
annotation에 복제하지 않는다. 디렉터리 전체를 일괄 apply하지 않는다.
인증된 활동 발행 202 후 consumer의 DB 처리와 실제 Kakao 로그인은 스크립트가 검증하지 않는다.

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
