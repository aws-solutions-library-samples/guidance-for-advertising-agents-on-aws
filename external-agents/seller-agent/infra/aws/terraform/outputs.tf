# -----------------------------------------------------------------------------
# Network outputs
# -----------------------------------------------------------------------------
output "vpc_id" {
  description = "ID of the VPC"
  value       = module.network.vpc_id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets"
  value       = module.network.public_subnet_ids
}

output "private_subnet_ids" {
  description = "IDs of the private subnets"
  value       = module.network.private_subnet_ids
}

# -----------------------------------------------------------------------------
# Storage outputs
# -----------------------------------------------------------------------------
output "aurora_cluster_endpoint" {
  description = "Aurora cluster writer endpoint"
  value       = module.storage.aurora_cluster_endpoint
}

output "aurora_cluster_reader_endpoint" {
  description = "Aurora cluster reader endpoint"
  value       = module.storage.aurora_cluster_reader_endpoint
}

output "redis_endpoint" {
  description = "ElastiCache Redis primary endpoint"
  value       = module.storage.redis_endpoint
}

# -----------------------------------------------------------------------------
# Compute outputs
# -----------------------------------------------------------------------------
output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = module.compute.alb_dns_name
}

output "alb_zone_id" {
  description = "Route 53 zone ID of the ALB (for alias records)"
  value       = module.compute.alb_zone_id
}

output "ecr_repository_url" {
  description = "URL of the ECR repository"
  value       = module.compute.ecr_repository_url
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = module.compute.ecs_cluster_name
}

output "ecs_service_name" {
  description = "Name of the ECS service"
  value       = module.compute.ecs_service_name
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group name for the ECS tasks"
  value       = module.compute.cloudwatch_log_group_name
}
