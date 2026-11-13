# Mosemo Terraform 실행 단위

| Root | 프로필 | 수명 | 관리 대상 | S3 state key |
| --- | --- | --- | --- | --- |
| admin/ | mosemo | 장기 | IAM 실행 역할·권한 | mosemo/iam/terraform.tfstate |
| foundation/ | mosemo-infra | 장기 | ECR 저장소, ACM 인증서·Cloudflare 검증 CNAME, ALB 접근 로그 S3 버킷 | mosemo/foundation/terraform.tfstate |
| infrastructure/ | mosemo-infra | 작업 세션 | VPC·EKS·노드 그룹·add-on·ALB·HTTPS 리스너·RDS·API CNAME | mosemo/lab/terraform.tfstate |

세 root는 `mosemo-tfstate-<ACCOUNT_ID>-ap-southeast-2` 버킷과 별도 lock key를 사용한다.
계정 ID와 Cloudflare zone ID는 커밋하지 않는다. 각 root에서 `backend.hcl.example`·
`terraform.tfvars.example`을 복사해 Git 제외 파일 `backend.hcl`·`terraform.tfvars`에 실제 값을 넣고
`init -backend-config=backend.hcl`로 초기화한다. 기존 초기화 디렉터리는 `-reconfigure`를 붙인다.
상위 디렉터리에서 Terraform을 실행하지 않는다.

## 수명과 순서

설정은 운영 실무와 같게 두고, 비용 때문에 필요할 때만 띄운다. 껐다 켜기 위한 처리는
설정이 아니라 `infra/deployment`의 세션 스크립트 절차에만 둔다.

1. admin: 실행 역할 권한. 변경은 admin 코드와 plan으로 검토하며 승인 전 적용하지 않는다.
2. foundation: 한 번 적용하고 유지한다. ECR 이미지, ACM 인증서(검증 CNAME 포함), ALB 접근 로그
   버킷(90일 수명 주기)은 세션 destroy와 무관하게 남는다.
3. infrastructure: 작업 세션마다 `session-up.sh`로 apply, `session-down.sh`로 destroy한다.
   foundation의 `ecr_repository_url`·`api_certificate_arn` 두 출력만 참조하므로 foundation이
   먼저 적용돼 있어야 한다. API CNAME `mosemo-api.danuh.com`(DNS only, TTL 300)은 ALB와
   같은 수명으로 Terraform이 만들고 지운다.

Cloudflare 변경은 `CLOUDFLARE_API_TOKEN`(danuh.com Zone:DNS:Edit 전용) 환경 변수로만 인증한다.
DNS 변경 승인은 각 plan 검토로 대신한다. `TF_VAR_db_password`는 세션 스크립트가 저장소 밖
비밀값 파일에서 읽어 프로세스 안에서만 주입한다. 값은 명령 인수·터미널·Git에 남기지 않는다.

```sh
terraform -chdir=infra/terraform/<root> init -backend-config=backend.hcl
terraform -chdir=infra/terraform/<root> fmt -check
terraform -chdir=infra/terraform/<root> validate
terraform -chdir=infra/terraform/<root> test
terraform -chdir=infra/terraform/<root> plan -input=false -lock-timeout=60s
```

구체적 구성은 [foundation](foundation/README.md), [infrastructure](infrastructure/README.md),
[admin](admin/README.md) README, 세션 절차는 [배포 문서](../deployment/README.md)를 참조한다.
