output "cluster_name" {
  description = "EKS cluster name for a future kubeconfig setup."
  value       = aws_eks_cluster.main.name
}

output "api_url" {
  description = "Canonical HTTPS URL; usable only after certificate validation, DNS, listener, and app deployment."
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
  value = aws_ecr_repository.server.repository_url
}


output "cloudflare_api_record" {
  description = "Create manually in Cloudflare only after DNS approval and HTTPS is ready."
  value = {
    name    = local.api_hostname
    type    = "CNAME"
    content = aws_lb.api.dns_name
    proxied = false
    ttl     = 300
  }
}

output "acm_validation_records" {
  description = "ACM-generated CNAMEs to register DNS only in Cloudflare after separate approval; preserve for renewal."
  value = [for record in aws_acm_certificate.api.domain_validation_options : {
    name    = record.resource_record_name
    type    = record.resource_record_type
    content = record.resource_record_value
    proxied = false
    ttl     = 300
  }]
}

output "kakao_redirect_uri" {
  value = "https://${local.api_hostname}/api/v1/auth/kakao/callback"
}

output "https_listener_enabled" {
  value = var.enable_https_listener
}
