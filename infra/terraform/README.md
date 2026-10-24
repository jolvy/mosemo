# Mosemo Terraform 실행 단위

| Root | 프로필 | 관리 대상 | S3 state key |
| --- | --- | --- | --- |
| admin/ | mosemo | IAM 실행 역할·권한 | mosemo/iam/terraform.tfstate |
| infrastructure/ | mosemo-infra | VPC·EKS·RDS·ECR·ALB·ACM | mosemo/lab/terraform.tfstate |

두 root는 `mosemo-tfstate-<ACCOUNT_ID>-ap-southeast-2` 버킷과 별도 lock key를 사용한다.
계정 ID는 커밋하지 않는다. 각 root에서 `backend.hcl.example`·`terraform.tfvars.example`을
복사해 Git 제외 파일 `backend.hcl`·`terraform.tfvars`에 실제 값을 넣고
`init -backend-config=backend.hcl`로 초기화한다. 기존 초기화 디렉터리는 `-reconfigure`를 붙인다.
상위 디렉터리에서 Terraform을 실행하지 않는다. admin IAM과 backend 버킷은 실습
인프라 정리 대상에서 제외한다. 권한 변경은 admin 코드와 plan으로 검토하며 승인 전 적용하지 않는다.

2026-10-22 사용자 요청으로 기존 배포 인프라 정리를 진행하고, origin/main의 Kafka
연동에 맞춘 API·consumer·단일 lab Kafka 구성은 **plan까지만** 검증한다.
추가 승인된 Kafka 영속 디스크는 gp3 5GiB·Retain·StatefulSet이며, admin에 전용
CSI Pod Identity 역할과 실행 권한, infrastructure에 add-on 2개·association 1개를
추가로 계획한다. 향후 적용 시 admin → infrastructure → Kubernetes 순서다.
최신 실행 결과는 저장소 밖 배포 로그와 로컬 HANDOFF.md에 기록한다.
이 작업에서는 재생성 apply·ECR push·Kubernetes 실제 배포를 하지 않는다.

```sh
terraform -chdir=infra/terraform/infrastructure init -backend-config=backend.hcl
terraform -chdir=infra/terraform/infrastructure fmt -check
terraform -chdir=infra/terraform/infrastructure validate
terraform -chdir=infra/terraform/infrastructure plan -input=false -lock-timeout=60s
```

계획 실행에는 `TF_VAR_db_password`를 안전한 launcher 내부에서 주입한다. 값은
명령 인수·터미널·Git에 남기지 않는다. db_password는 sensitive·ephemeral이며
RDS password_wo로 전달한다. 실제 재생성은 별도 요청 전까지 실행하지 않는다.

구체적 구성은 [infrastructure README](infrastructure/README.md),
앱 실행 순서는 [배포 문서](../deployment/README.md)를 참조한다.


2026-10-23 사용자 선택 API 호스트는 `mosemo-api.danuh.com`이다. Cloudflare DNS를 유지하고
ACM DNS validation 및 ALB 443/HTTP redirect를 plan까지만 준비한다. DNS 변경과 apply는
별도 승인 대상이다. [HTTPS 단계 설명](infrastructure/README.md#cloudflare-dns와-https-준비-2026-10-23-미적용)을 따른다.
