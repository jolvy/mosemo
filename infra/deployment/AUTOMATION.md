# 무중단 배포 자동화 비교와 도입 제안

기준일: 2026-10-23 KST. 대상은 AWS HTTPS 서비스의 애플리케이션 배포이며, 요구 조건은
**무중단 배포**다. 이 문서는 비교·제안이며 적용 기록이 아니다.

## 자동화 실행 도구 비교

배포 도구와 무중단 전환 전략은 별개의 선택이다. 도구만 바꿔서는 용량과
Pod 종료 순서 때문에 생기는 중단을 없앨 수 없다.

| 방식 | 장점 | 추가 구성·제약 | 판단 |
| --- | --- | --- | --- |
| GitHub Actions + AWS OIDC | 기존 CI 재사용, 상주 Pod 불필요, 장기 AWS 키 없이 임시 자격증명 | OIDC 공급자·전용 IAM 역할·EKS 접근·배포 직렬화 필요. 현재 조직 정책으로 OIDC 구성을 관리할 수 없음 | 조직 정책 해소 후 우선 검토 |
| CodePipeline V2 + CodeBuild + EKS action | AWS 내부에서 빌드·배포 순서와 승인 관리 | CodeConnections 연결과 Console OAuth, artifact 저장소·역할·별도 요금 | AWS 중심 운영을 원할 때 대안 |
| Argo CD / Flux GitOps | Git 기준 지속 동기화와 drift 관리 | controller·권한·설정·업데이트 운영 필요, 노드 여유 필요 | 지금 단계에서는 보류 |
| 로컬 배포 명령 + 현재 AWS 로그인 | 추가 서비스·장기 키 없이 현재 권한 활용, 이후 CI에서 같은 절차 재사용 가능 | 실행 PC·로그인·동시 실행 방지 필요. 자동 트리거가 있는 CD와는 구분 | 정책 해소 전 도입 가능한 첫 단계 |

근거:
- [GitHub AWS OIDC](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [CodePipeline EKS action](https://docs.aws.amazon.com/codepipeline/latest/userguide/action-reference-EKS.html)
- [CodePipeline EKS tutorial](https://docs.aws.amazon.com/codepipeline/latest/userguide/tutorials-eks-deploy.html)
- [Argo CD 설치 구성](https://argo-cd.readthedocs.io/en/stable/operator-manual/installation/)
- [Flux 구성요소](https://fluxcd.io/flux/components/)

IAM 역할에 Allow를 추가해 조직 정책(SCP)의 명시적 Deny를 덮을 수는 없다. 장기 AWS 키를
GitHub Secrets에 넣는 우회는 제안하지 않는다.

## 배포 전략 비교

| 전략 | 무중단에 필요한 조건 | 판단 |
| --- | --- | --- |
| Rolling update | 새 Pod 실행 용량, maxUnavailable=0, readiness·종료 대기, 버전 간 호환성 | 추가 노드 확보 후 추천. 기존 NodePort·ALB 재사용 |
| Blue/green | 이전·새 API를 병렬 운영하고 검증 후 트래픽 전환, 양쪽 DB 호환 | preview 경로·Service·전환 절차가 추가됨. API 1개에는 rolling보다 구성이 많음 |
| Canary | 트래픽 가중치 전환, 성공률·지연 지표, 실패 판정·복구 | 관측·트래픽 분할 기반부터 마련해야 함 |
| Recreate / consumer 중지 후 migration | 기존 프로세스를 먼저 중지 | 무중단 조건 때문에 제외 |

[Kubernetes rolling update](https://kubernetes.io/docs/tasks/run-application/update-deployment-rolling/)는
기존 Pod를 유지하며 새 Pod로 전환한다. maxUnavailable=0과 maxSurge=1만으로
용량·readiness·연결 종료·DB 호환성까지 보장되는 것은 아니다.

## 제안: 노드 추가 후 rolling update

범위는 **API·consumer 릴리스 중 외부 API 요청을 끊지 않는 것**이다. 노드·AZ·RDS·Kafka
장애까지 포함한 상시 고가용성은 별도 범위다. consumer 교체의 Kafka rebalance에는 일시적인
처리 지연이 있을 수 있으므로 HTTP 가용성과 처리 지연·유실을 구분해 측정한다.

용량: 같은 AZ에 t3.small 1대를 추가해 앱 교체본과 migration 실행 공간을 상시 확보한다.
단일 t3.medium은 노드 교체·Kafka EBS 재연결이 필요하고, 배포 때만 노드를 늘리는 방식은
시작 지연과 축소 대상 선정 문제가 있어 제외한다. 추가 비용은 t3.small·gp3 20GiB·공인 IPv4
1개 기준 월 약 24.84 USD(730시간, 시드니 온디맨드, 세금·전송량·할인 제외)다.

변경 범위:

1. app 노드 그룹에 t3.small 1대를 상시 추가한다.
2. API Deployment를 maxSurge=1, maxUnavailable=0으로 바꾸고 minReadySeconds·rollout timeout,
   preStop 대기와 uvicorn 종료 timeout을 terminationGracePeriod 안에 배치한다.
3. consumer도 기존 Pod를 먼저 내리지 않고 교체하고, Running이 아니라 구독·group/offset·실제
   처리로 성공을 확인한다.
4. linux/amd64 빌드 → ECR push → digest 고정 → migration Job 성공 → API rollout → consumer
   rollout → HTTPS·인증·메시지 처리 검증 순으로 실행한다. migration 실패 시 앱 교체를
   시작하지 않고, DB는 자동 downgrade하지 않는다.
5. 이전·새 버전 모두 호환되는 expand/contract migration만 이 절차로 배포한다.
6. API readiness는 실제 시작 완료를 반영하고, DB·Kafka를 liveness에 묶지 않는다.
7. 조직 정책이 해소되면 같은 명령을 GitHub Actions에 연결한다. OIDC trust는 특정 repo·main
   또는 보호된 Environment로 제한하고, 배포 역할은 ECR 저장소와 EKS 앱 배포 범위로 한정해
   Terraform 관리자 역할과 분리한다.

NodePort 구성에서는 Pod endpoint 전파와 기존 연결 drain을 확인해야 하며, ALB deregistration
delay만 늘리는 것으로 해결됐다고 판단하지 않는다.
[ALB target 속성](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/edit-target-group-attributes.html).

## 무중단 검증 기준

- 이미지 교체 전부터 종료 후까지 HTTPS 연속 요청에서 연결 실패·예상 밖 5xx가 없고,
  인증된 조회·활동 발행/최종 처리·Kakao 복귀가 유지되는지 측정한다.
- 실패 경로: 새 Pod readiness 실패 시 기존 API 유지, migration 실패 시 앱 버전 유지,
  rollout timeout 시 진단·이전 digest 복귀. DB 호환성을 확인하지 않은 자동 rollback은 금지한다.
- 유한한 테스트 구간에서 오류가 없었던 결과와 모든 장애에서의 무중단 보장은 구분한다.
