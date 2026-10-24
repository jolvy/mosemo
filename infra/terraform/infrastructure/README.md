# Mosemo AWS 실습 인프라

계정 `<ACCOUNT_ID>`, 시드니 `ap-southeast-2`, AWS profile `mosemo-infra`를 사용한다.
기존 리소스는 2026-10-22 비용 절감을 위해 정리하며, 이후에는 **plan까지만** 실행한다.
현재 실행 결과는 로컬 HANDOFF.md와 저장소 밖 로그를 기준으로 확인한다.

## 계획 구성

- VPC 10.20.0.0/16. public subnet 10.20.0.0/22·10.20.4.0/22,
  private subnet 10.20.16.0/20·10.20.32.0/20, AZ a·b.
- public subnet은 Internet Gateway, private subnet은 VPC 내부 경로만 사용한다.
  NAT Gateway·VPC Endpoint는 없다.
- EKS 1.36, AL2023 amd64, 사용자 지정 `t3.small` 1대. API·consumer 각 224Mi,
  Kafka 512Mi를 요청하며 Kafka JVM heap은 최대 256Mi다. 작은 실습 구성으로
  부하 여유·장시간 안정성은 별도 검증해야 한다. 노드 크기 변경은 하지 않는다.
  EBS AZ에 맞춰 node group은 public-a(ap-southeast-2a)만 사용한다.
- RDS PostgreSQL 18.3, db.t4g.micro, 암호화 gp3 20GiB, private Single-AZ.
- public HTTPS ALB → NodePort HTTP 30080 → API Pod 8000. HTTP 80은 확정된 HTTPS 호스트로 301 redirect. consumer·Kafka는 외부 공개하지 않는다.
- ECR mosemo-lab-server. API·consumer·migration은 새로 빌드한 동일 digest 사용.
- 단일 Kafka Pod와 topic 초기화 Job은 Kubernetes manifest로만 준비한다.
  Kafka는 StatefulSet·gp3 5GiB PVC·Retain 정책을 사용한다. Terraform은 Pod Identity
  Agent·EBS CSI add-on과 전용 service account association 3개를 추가로 계획한다.
  Kubernetes가 만드는 EBS 볼륨은 이 Terraform state가 관리하지 않는다.
  MSK·Redis·label proposal worker·별도 batch worker·CI/CD는 추가하지 않는다.
  DNS는 Cloudflare에 유지하고 ACM 인증서와 ALB HTTPS만 AWS에 준비한다.

## State와 권한

S3 backend 버킷 `mosemo-tfstate-<ACCOUNT_ID>-ap-southeast-2`, key
`mosemo/lab/terraform.tfstate`, S3 Native Locking을 사용한다. 별도 admin root의 IAM과
backend 버킷은 유지한다. 정리 후에도 state 이력의 소량 저장·요청 비용은 남을 수 있다.
기본 kubeconfig를 바꾸지 말고 배포 전용 KUBECONFIG를 사용한다.

```sh
terraform -chdir=infra/terraform/infrastructure init -backend-config=backend.hcl
terraform -chdir=infra/terraform/infrastructure fmt -check
terraform -chdir=infra/terraform/infrastructure validate
terraform -chdir=infra/terraform/infrastructure plan -input=false -lock-timeout=60s
```

TF_VAR_db_password는 실행 프로세스 내부에서만 파일로부터 읽는다.
비밀번호는 plan·state에 저장하지 않는 ephemeral 변수와 password_wo를 사용한다.
실제 배포에 사용할 비밀번호는 URL 안전 문자로 생성하고 RDS와 앱 Secret에 동일하게 넣는다.
이번 검증에서 `apply`, `-target`, `-lock=false`, refresh 생략은 사용하지 않는다.
이전 saved plan·이전 ECR digest·삭제한 ALB DNS를 재배포에 재사용하지 않는다.

## 수명과 검증 한계

실습용 RDS는 백업 0일·삭제 보호 없음·최종 스냅샷 없음이다. 삭제하면 데이터가 사라진다.
ECR은 force_delete=true여서 이미지도 함께 삭제된다. 삭제 plan과 재생성 plan을 구별한다.
Terraform plan은 AWS 기반 인프라만 검사하며 Kubernetes 스케줄링·Kafka 처리·로그인을
검증하지 않는다. 앱 manifest 검증과 로컬 테스트는 [배포 문서](../../deployment/README.md)를 따른다.


## Cloudflare DNS와 HTTPS 준비 (2026-10-23, 미적용)

확정 API 주소는 `https://mosemo-api.danuh.com`이다. Cloudflare plugin으로 danuh.com의
active/full 상태와 기존 DNS 0개를 조회했고, 공개 NS도 Cloudflare 위임과 일치했다.
Route 53 zone·레코드·네임서버 이전은 만들지 않는다. zone SSL 모드·프록시·WAF·플랜도 바꾸지 않는다.

ACM은 시드니의 DNS validation public RSA 2048 인증서, export DISABLED로 준비한다.
ALB TLS 정책은 ELBSecurityPolicy-TLS13-1-2-2021-06(TLS 1.2/1.3)이며 443만 API로 전달한다.
80은 고정 호스트로 301 redirect하고 경로·쿼리를 유지한다. 클라이언트는 처음부터 HTTPS URL을
사용한다. ALB→노드 30080 구간은 기존 HTTP와 보안 그룹 제한을 유지한다.

외부 DNS 승인을 기다리는 apply가 막히지 않도록 두 단계가 있다. **아래는 순서 설명이며
현재 apply·DNS 변경 승인이나 실행을 의미하지 않는다.** 각 단계에 새 plan과 별도 승인이 필요하다.

1. admin의 CSI 및 https IAM 변경을 검토·승인 후 적용한다. 인프라 역할이 자기 권한을 바꾸지 않는다.
2. `-var=enable_https_listener=false`로 새 infrastructure plan을 검토한다. 향후 승인된 적용 시
   기존 예정 인프라와 ACM 인증서를 생성하되 certificate validation waiter·443은 만들지 않는다.
   **인증서만 생성하는 모드가 아니며 EKS·RDS·ALB 등 전체 인프라 비용이 시작된다.**
   이 단계의 80은 HTTPS로 redirect만 하며 443이 아직 없어 서비스는 사용 불가다.
3. ACM이 생성한 `acm_validation_records` 출력의 CNAME을 별도 승인 후 Cloudflare에
   DNS only, TTL 300으로 등록한다. 인증서 갱신을 위해 이 레코드를 유지한다. 토큰을 추측하지 않는다.
4. DNS 전파 후 기본값 `enable_https_listener=true`의 새 plan을 승인받는다. 적용 시 최대 10분
   validation waiter가 ISSUED를 확인한 뒤 443 리스너를 연결한다. 실패 시 DNS·CAA를 확인하고
   무조건 재시도하거나 validation을 건너뛰지 않는다. 이후 false로 돌리면 443이 제거되므로 금지한다.
5. 앱 배포·target health 확인 후 `cloudflare_api_record` 출력의 CNAME을 별도 승인 후
   Cloudflare에 DNS only, TTL 300으로 등록한다. 새 ALB DNS만 사용한다.
6. DNS·TLS hostname/chain·HTTP redirect·HTTPS 응답을 각각 검증한다. 앱 미배포/unhealthy와 TLS를 구분한다.

`api_url`은 확정 주소이며 서비스 준비 여부를 보장하지 않는다. macOS 앱 빌드 설정
`MOSEMO_API_BASE_URL=https://mosemo-api.danuh.com`, Kakao 서버 Redirect URI는
`https://mosemo-api.danuh.com/api/v1/auth/kakao/callback`이다. 실제 Kakao 등록은 사용자가 한다.

참조: [ACM DNS validation](https://docs.aws.amazon.com/acm/latest/userguide/dns-validation.html),
[DNS renewal](https://docs.aws.amazon.com/acm/latest/userguide/dns-renewal-validation.html),
[ALB TLS policies](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/describe-ssl-policies.html).
