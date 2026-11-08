output "cluster_name" {
  description = "EKS cluster name for a future kubeconfig setup."
  value       = aws_eks_cluster.main.name
}

output "api_url" {
  description = "Canonical HTTPS URL; serves traffic only after the app is deployed."
  value       = "https://${local.api_hostname}"
}

output "api_node_port" {
  description = "Use this nodePort in the future Kubernetes Service (externalTrafficPolicy: Cluster)."
  value       = local.api_node_port
}

output "database" {
  description = "Map these values to the application's DB_* environment variables."
  value = {
    DB_HOST     = aws_db_instance.main.address
    DB_PORT     = aws_db_instance.main.port
    DB_DATABASE = aws_db_instance.main.db_name
    DB_USER     = aws_db_instance.main.username
  }
}

output "ecr_repository_url" {
  description = "Long-lived repository from ../foundation."
  value       = data.terraform_remote_state.foundation.outputs.ecr_repository_url
}

output "alb_dns_name" {
  description = "Target of the Terraform-managed Cloudflare CNAME; used for DNS-independent smoke checks."
  value       = aws_lb.api.dns_name
}

output "kakao_redirect_uri" {
  value = "https://${local.api_hostname}/api/v1/auth/kakao/callback"
}
