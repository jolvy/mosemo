# Mosemo AWS 세션 스택

계정 `<ACCOUNT_ID>`, 시드니 `ap-southeast-2`, AWS profile `mosemo-infra`를 사용한다.
작업 세션마다 [session-up.sh](../../deployment/session-up.sh)로 apply하고
[session-down.sh](../../deployment/session-down.sh)로 destroy한다. 장기 리소스(ECR·ACM)는
[foundation](../foundation/README.md)에 있으며 이 스택은 그 출력 두 개만 참조한다.

## 구성

- VPC 10.20.0.0/16. public subnet 10.20.0.0/22·10.20.4.0/22,
  private subnet 10.20.16.0/20·10.20.32.0/20, AZ a·b.
- public subnet은 Internet Gateway, private subnet은 VPC 내부 경로만 사용한다.
  NAT Gateway·VPC Endpoint는 없다.
- EKS 1.36, AL2023 amd64. 관리형 노드 그룹 2개, 모두 On-Demand `t3.small`:

  | 그룹 | 크기 (min/desired/max) | subnet | label·taint | 용도 |
  | --- | --- | --- | --- | --- |
  | mosemo-lab-kafka | 1/1/1 | public-a | `pool=kafka`, `dedicated=kafka:NoSchedule` | Kafka StatefulSet (EBS가 2a에 고정) |
  | mosemo-lab-app | 1/1/2 | public-a·b | `pool=app` | API·consumer·migration·시스템 add-on, ALB target |

  두 그룹은 공유 launch template `mosemo-lab-node`를 쓴다. AMI·user data·instance type은 지정하지
  않고 `cpu_credits=standard`, IMDSv2 강제(hop limit 1, Pod 네트워크에서 노드 자격증명 차단),
  20GiB gp3 root volume만 설정한다. EKS가 bootstrap user data와
  max-pods를 계속 계산한다.
- vpc-cni add-on을 Terraform이 관리하고 `ENABLE_PREFIX_DELEGATION=true`로 노드 그룹보다 먼저
  만든다. 관리형 노드 그룹은 AMI 미지정 launch template이면 max-pods를 자동 계산하며,
  vCPU 30 미만 인스턴스는 110으로 제한한다. t3.small 기본 상한 11개 대신 110개가 기대값이며
  session-up.sh가 노드의 allocatable pods로 확인한다.
- Pod Identity Agent·EBS CSI add-on과 전용 controller association. CSI node plugin은
  Kafka 노드 taint를 견디도록 `tolerateAllTaints=true`.
- RDS PostgreSQL 18.3, db.t4g.micro, 암호화 gp3 20GiB, private Single-AZ.
  자동 백업 7일, 삭제 보호, destroy 시 최종 스냅샷 `mosemo-lab-final`이 기본이다.
- public HTTPS ALB → NodePort HTTP 30080 → API Pod 8000. app 그룹 ASG만 target group에
  연결한다. HTTP 80은 HTTPS 호스트로 301 redirect, 443은 TLS 1.2/1.3
  (`ELBSecurityPolicy-TLS13-1-2-2021-06`)과 foundation의 검증 완료 인증서를 첫 apply부터 쓴다.
- Cloudflare `mosemo-api.danuh.com` CNAME → ALB DNS, DNS only, TTL 300. ALB와 같은 수명이다.

## 실행

`terraform.tfvars`에 `aws_account_id`·`cloudflare_zone_id`, 환경 변수 `CLOUDFLARE_API_TOKEN`이
필요하다. `TF_VAR_db_password`는 ephemeral 변수로 plan·state에 저장되지 않으며 RDS password_wo로
전달한다. 세션 스크립트가 저장소 밖 비밀값 파일에서 읽어 주입한다.

```sh
terraform -chdir=infra/terraform/infrastructure init -backend-config=backend.hcl
terraform -chdir=infra/terraform/infrastructure test
terraform -chdir=infra/terraform/infrastructure plan -input=false -lock-timeout=60s
```

`-target`, `-lock=false`, refresh 생략은 사용하지 않는다. 이전 saved plan·ALB DNS를 재사용하지 않는다.

## 세션 종료와 데이터

설정은 운영과 같은 보호(RDS 삭제 보호·최종 스냅샷, Kafka PV Retain)를 유지한다. 보호 해제는
session-down.sh 절차에서만 한다. RDS의 `skip_final_snapshot`은 provider가 destroy 때 state 값을
읽으므로, 스크립트가 `-var skip_final_snapshot=true` plan의 변경이 `aws_db_instance.main` update
하나뿐인지 확인하고 apply해 state에 반영한 뒤, 삭제 보호를 해제하고 같은 변수로 destroy한다. 세션 간 DB·Kafka 데이터는 보존하지 않는다.

Terraform plan은 AWS 기반 인프라만 검사하며 Kubernetes 스케줄링·Kafka 처리·로그인을
검증하지 않는다. 앱 절차는 [배포 문서](../../deployment/README.md)를 따른다.

`api_url`은 확정 주소이며 서비스 준비 여부를 보장하지 않는다. macOS 앱 빌드 설정
`MOSEMO_API_BASE_URL=https://mosemo-api.danuh.com`, Kakao 서버 Redirect URI는
`https://mosemo-api.danuh.com/api/v1/auth/kakao/callback`이다. 실제 Kakao 등록은 사용자가 한다.

참조: [prefix delegation](https://docs.aws.amazon.com/eks/latest/userguide/cni-increase-ip-addresses-procedure.html),
[maxPods 결정 순서](https://docs.aws.amazon.com/eks/latest/userguide/choosing-instance-type.html#max-pods-precedence),
[launch template](https://docs.aws.amazon.com/eks/latest/userguide/launch-templates.html),
[ALB TLS policies](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/describe-ssl-policies.html).
