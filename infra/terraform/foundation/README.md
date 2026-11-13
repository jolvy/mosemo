# Mosemo 장기 스택 (foundation)

세션 스택(`../infrastructure`)을 destroy해도 유지할 리소스만 둔다. 계정 `<ACCOUNT_ID>`,
시드니 `ap-southeast-2`, AWS profile `mosemo-infra`, state key `mosemo/foundation/terraform.tfstate`.

- ECR `mosemo-lab-server`. API·consumer·migration이 같은 digest를 사용한다.
  deploy.sh가 커밋 SHA 태그로 이미지를 찾고, 없을 때만 빌드·push한다. 태그는 IMMUTABLE,
  push 때 기본 스캔을 하며, 이미지가 남아 있으면 저장소 삭제가 실패한다(`force_delete = false`).
- S3 `mosemo-lab-alb-logs-<ACCOUNT_ID>`: 세션 ALB 접근 로그(prefix `mosemo-lab`). 공개 접근 차단 4개,
  BucketOwnerEnforced, SSE-S3(ALB 로그는 SSE-KMS 미지원), 전체 객체 90일 만료, `force_destroy = false`.
  버킷 정책은 AWS 현재 권장인 서비스 principal `logdelivery.elasticloadbalancing.amazonaws.com`에
  `mosemo-lab/AWSLogs/<ACCOUNT_ID>/*` 쓰기만 주고 `aws:SourceArn`을 이 계정 시드니 ALB로 제한한다
  (리전별 ELB 계정 principal은 AWS 문서상 legacy).
- ACM `mosemo-api.danuh.com`: 시드니 public RSA 2048, DNS validation, export DISABLED.
  검증 CNAME은 Cloudflare provider로 DNS only·TTL 300으로 만들고, 인증서 갱신을 위해 유지한다.
  `aws_acm_certificate_validation`이 최대 10분 동안 ISSUED를 기다린다. 실패하면 DNS·CAA를
  확인하고 validation을 건너뛰지 않는다.
- Route 53 zone·레코드·네임서버 이전은 만들지 않는다. zone SSL 모드·프록시·WAF·플랜도 바꾸지 않는다.

출력은 세션 스택이 읽는 값뿐이다: `ecr_repository_url`, `api_certificate_arn`(검증 완료 인증서), `alb_access_log_bucket`.

## 실행

`terraform.tfvars`에 `aws_account_id`·`cloudflare_zone_id`(danuh.com), 환경 변수에
`CLOUDFLARE_API_TOKEN`이 필요하다. 첫 적용은 admin의 state·권한 변경이 적용된 뒤에 한다.

```sh
terraform -chdir=infra/terraform/foundation init -backend-config=backend.hcl
terraform -chdir=infra/terraform/foundation test
terraform -chdir=infra/terraform/foundation plan -input=false -lock-timeout=60s
```

참조: [ACM DNS validation](https://docs.aws.amazon.com/acm/latest/userguide/dns-validation.html),
[DNS renewal](https://docs.aws.amazon.com/acm/latest/userguide/dns-renewal-validation.html),
[ALB access logs](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/enable-access-logging.html).
