output "alb_dns_name" {
  description = "URL to hit once the service is healthy — e.g. http://<this>/health or http://<this>/ask"
  value       = "http://${aws_lb.app.dns_name}"
}

output "ecr_repository_url" {
  description = "Push your built image here (see README.md) before the ECS service can pull it."
  value       = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  value = aws_ecs_service.app.name
}
