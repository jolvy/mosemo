# Mosemo IAM 초기 설정

계정 <ACCOUNT_ID> / 시드니 ap-southeast-2, 관리자 profile mosemo로 관리한다.
기존 IAM 15개는 유지하고 EBS CSI 및 HTTPS용 역할·정책 변경을 **plan까지만** 준비한다.
이번 작업에서 IAM apply·계정·조직·플랜·고급 기능 변경은 하지 않는다.

## 역할과 권한 분리

- MosemoInfrastructureRole: 기존 managed/AccountFullAccessRole만 맡는 1시간 실행 역할.
- mosemo-lab-eks-cluster: AmazonEKSClusterPolicy.
- mosemo-lab-eks-node: AmazonEKSWorkerNodePolicy·ECR PullOnly·기존 CNI policy.
- **추가 계획** mosemo-lab-ebs-csi: pods.eks.amazonaws.com의 AssumeRole·TagSession 신뢰.
  request tag의 cluster ARN·kube-system·ebs-csi-controller-sa를 모두 요구한다.
  AWS 권장 AmazonEBSCSIDriverPolicyV2를 연결하며 node 역할에는 EBS 권한을 추가하지 않는다.

인프라 실행 역할에는 일반 IAM 생성·정책 변경·조직/결제 관리 권한을 주지 않는다.
기존 cluster/node PassRole과 별도로 전용 CSI 역할을 pods.eks.amazonaws.com에
전달하는 권한만 추가한다. 실제 역할과 정책 연결은 이 admin root가 소유한다.

| 실행 역할 정책 | 범위 |
| --- | --- |
| network | 기존 시드니 VPC·subnet·routing·security group |
| services | 지정 EKS·node group(kafka·app)·ALB·RDS(최종 스냅샷 `mosemo-lab-final` 포함)·ECR 수명주기, app ASG의 ALB 연결 |
| identity | 지정 역할 조회·PassRole·기존 service-linked role 초기화 |
| state | 세션(mosemo/lab)·장기(mosemo/foundation) state key와 lock |
| addons | 지정 cluster의 add-on/Pod Identity 생성, vpc-cni·EBS CSI·Pod Identity Agent add-on 및 association 관리 |
| https | 선택한 API 도메인의 ACM 요청 및 지정 태그 인증서 수명주기 |
| nodes (G0 신규) | 프로젝트 태그를 붙인 노드 launch template 생성·버전·삭제 |
| volumes (G0 신규) | 세션 종료 시 Retain으로 남은 Kafka PVC EBS 조회·삭제 |

AWS는 CreateAddon에서 addon 이름, CreatePodIdentityAssociation에서 namespace/SA를
조건으로 제한하는 IAM condition key를 제공하지 않는다. 생성 권한은 정확한 cluster ARN과
Project=mosemo·Environment=lab·ManagedBy=terraform 요청 태그로 제한한다.
생성 이후 add-on 관리는 aws-ebs-csi-driver·eks-pod-identity-agent ARN으로 제한하고,
association은 해당 cluster ARN 범위, PassRole은 CSI 역할 하나, 실제 역할 인수는 trust
policy의 서비스 계정/cluster 태그로 제한한다. 이 생성 권한을 이름 제한이라고 해석하지 않는다.
AWS managed CSI 정책의 권한은 해당 controller 역할이 가지며 인프라 실행 역할이 직접
EBS 생성·삭제 권한을 얻는 것은 아니다. 정책이 허용해도 실제 AWS/SCP 조건이 우선한다.

## 실행 순서와 검증

향후 재배포 요청 시 admin 변경을 먼저 적용한 뒤 infrastructure를 적용해야 한다.
새 CSI 역할은 아직 없으므로 infrastructure에서는 알려진 role ARN을 참조해 plan한다.
plan 성공은 아직 적용하지 않은 권한의 실제 생성·연결 성공을 증명하지 않는다.

```sh
terraform -chdir=infra/terraform/admin init -backend-config=backend.hcl
terraform -chdir=infra/terraform/admin fmt -check
terraform -chdir=infra/terraform/admin validate
terraform -chdir=infra/terraform/admin test
terraform -chdir=infra/terraform/admin plan -input=false -lock-timeout=60s
```

2026-10-23 state 기준 계획은 6개 생성, 1개 변경, 삭제 0개였다. G0 변경(아래)의 plan 결과는
적용 전 새로 확인한다.
mock test는 정책 크기·wildcard action 금지·리전·state·역할 trust 경계를 검사한다.
추가 addons/identity 정책은 Access Analyzer validate-policy에서 findings []를 확인했다.
실제 IAM 변경이나 EBS 작업은 실행하지 않는다.

## State와 비밀값

S3 backend는 기존 mosemo-tfstate-<ACCOUNT_ID>-ap-southeast-2,
key mosemo/iam/terraform.tfstate, profile mosemo, Native Locking이다.
앱 root는 mosemo/lab/terraform.tfstate를 사용하며 IAM key 접근 권한을 갖지 않는다.
버킷·이력은 유지하고 backend 재생성·state 강제 변경·기존 saved plan 재사용은 하지 않는다.
state·plan·환경 파일·로컬 handoff는 Git에 넣지 않는다.

참고: [EBS CSI 설정](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html),
[Pod Identity trust](https://docs.aws.amazon.com/eks/latest/userguide/pod-id-role.html).


## HTTPS 추가 준비 (미적용)

https managed policy와 실행 역할 attachment를 추가로 계획한다. ACM 요청은 시드니·
`mosemo-api.danuh.com` 단일 이름·DNS validation·RSA_2048·export DISABLED 및 지정 태그로 제한한다.
ForAllValues의 빈 집합 허용을 막기 위해 acm:DomainNames Null=false도 요구한다.
public 인증서만 요청하도록 acm:CertificateAuthority Null=true를 요구한다.
조회는 같은 계정/리전의 인증서 ARN 범위, 삭제·옵션 변경·기존 태그 관리는 지정 프로젝트와
hostname 태그의 인증서로 제한한다. 최초 태그는 RequestCertificate 요청에 함께 전달한다. 독립 AddTagsToCertificate는 이미 지정 태그가
있는 인증서에만 허용해 다른 인증서를 태그로 편입하지 못하게 한다.
이 IAM 코드는 아직 실제 적용하지 않았다. 기존 권한으로 빈 state의 plan이 성공해도
향후 ACM 생성·조회 권한이 반영됐다는 뜻은 아니다. Cloudflare나 Route 53 권한은 추가하지 않는다.

생성 시 inline Tags에 별도 AddTags 권한이 필요한지는 AWS 문서의 API 권한표와
Service Authorization operation mapping 표현에 차이가 있다. 이 차이를 이유로
다른 인증서에 태그를 붙일 권한을 열지 않는다. 실제 최초 생성 권한은 apply 승인 후 확인하고,
거부되면 권한 확대나 관리자 우회 없이 중단해 원인을 보고한다.

## G0 노드 그룹·세션 권한 (미적용)

- services: node group ARN을 `mosemo-lab-public`에서 `mosemo-lab-kafka`·`mosemo-lab-app`으로
  바꾼다. ALB target group 연결은 `eks-mosemo-lab-app-*` ASG로만 제한해 Kafka 노드가 트래픽을
  받지 않게 한다. RDS 최종 스냅샷은 이름 `mosemo-lab-final` 하나에만 CreateDBSnapshot을 허용한다.
- addons: vpc-cni add-on ARN을 관리 대상에 추가한다(prefix delegation).
- nodes: `ec2:CreateLaunchTemplate`은 Project·Environment·ManagedBy 요청 태그를 요구하고,
  CreateTags는 CreateLaunchTemplate 생성 시점에만, 버전 생성·수정·삭제는 같은 태그가 있는
  launch template에만 허용한다. launch template ARN은 ID 기반이라 이름 제한은 할 수 없다.
  `ec2:RunInstances`는 주지 않는다. launch template에 TagSpecification을 두지 않으므로 EKS 문서상
  CreateNodegroup 호출자에게 필요하지 않다. 적용 후 거부되면 권한 확대 없이 중단해 보고한다.
- volumes: DescribeVolumes(리전 제한)와 DeleteVolume. 삭제는 CSI가 붙인 태그
  `ebs.csi.aws.com/cluster=true`, `kubernetes.io/created-for/pvc/namespace=mosemo`,
  `kubernetes.io/created-for/pvc/name=data-mosemo-kafka-0`가 모두 있는 볼륨으로 제한한다.
- state: foundation state key와 lock 읽기·쓰기를 추가한다. IAM state key 접근은 계속 없다.

향후 적용 순서는 admin → foundation → 세션 스택이다.
