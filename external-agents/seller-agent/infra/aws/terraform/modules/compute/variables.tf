variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for the ALB"
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for ECS tasks"
  type        = list(string)
}

variable "alb_security_group_id" {
  description = "Security group ID for the ALB"
  type        = string
}

variable "ecs_security_group_id" {
  description = "Security group ID for ECS tasks"
  type        = string
}

variable "container_image_tag" {
  description = "Docker image tag to deploy"
  type        = string
}

variable "container_port" {
  description = "Port the container listens on"
  type        = number
}

variable "desired_count" {
  description = "Number of ECS tasks"
  type        = number
}

variable "cpu" {
  description = "Fargate task CPU units"
  type        = number
}

variable "memory" {
  description = "Fargate task memory in MiB"
  type        = number
}

variable "health_check_path" {
  description = "ALB health check path"
  type        = string
}

variable "database_endpoint" {
  description = "Aurora cluster writer endpoint"
  type        = string
}

variable "database_port" {
  description = "Aurora cluster port"
  type        = number
}

variable "database_name" {
  description = "Database name"
  type        = string
}

variable "database_username" {
  description = "Database master username"
  type        = string
}

variable "redis_endpoint" {
  description = "ElastiCache Redis endpoint"
  type        = string
}

variable "redis_port" {
  description = "ElastiCache Redis port"
  type        = number
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS (empty string for HTTP only)"
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
}
