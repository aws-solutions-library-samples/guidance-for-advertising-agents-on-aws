# -----------------------------------------------------------------------------
# General
# -----------------------------------------------------------------------------
variable "environment" {
  description = "Deployment environment (e.g. dev, staging, prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be one of: dev, staging, prod."
  }
}

variable "region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

# -----------------------------------------------------------------------------
# Network
# -----------------------------------------------------------------------------
variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of two availability zones"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (one per AZ)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (one per AZ)"
  type        = list(string)
  default     = ["10.0.10.0/24", "10.0.11.0/24"]
}

# -----------------------------------------------------------------------------
# Database (Aurora Serverless v2)
# -----------------------------------------------------------------------------
variable "db_name" {
  description = "Name of the PostgreSQL database"
  type        = string
  default     = "ad_seller"
}

variable "db_master_username" {
  description = "Master username for Aurora"
  type        = string
  default     = "seller"
}

variable "aurora_min_capacity" {
  description = "Aurora Serverless v2 minimum ACU"
  type        = number
  default     = 0.5
}

variable "aurora_max_capacity" {
  description = "Aurora Serverless v2 maximum ACU"
  type        = number
  default     = 4
}

# -----------------------------------------------------------------------------
# Redis
# -----------------------------------------------------------------------------
variable "redis_node_type" {
  description = "ElastiCache Redis node type"
  type        = string
  default     = "cache.t3.micro"
}

# -----------------------------------------------------------------------------
# Compute (ECS / Fargate)
# -----------------------------------------------------------------------------
variable "container_image_tag" {
  description = "Docker image tag to deploy (the ECR repo is created by the compute module)"
  type        = string
  default     = "latest"
}

variable "container_port" {
  description = "Port the application listens on"
  type        = number
  default     = 8000
}

variable "desired_count" {
  description = "Number of ECS tasks to run"
  type        = number
  default     = 2
}

variable "task_cpu" {
  description = "Fargate task CPU units (1024 = 1 vCPU)"
  type        = number
  default     = 256
}

variable "task_memory" {
  description = "Fargate task memory in MiB"
  type        = number
  default     = 512
}

variable "health_check_path" {
  description = "HTTP path used by the ALB health check"
  type        = string
  default     = "/health"
}

# -----------------------------------------------------------------------------
# TLS / ALB
# -----------------------------------------------------------------------------
variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS listener (leave empty to create HTTP-only listener)"
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Observability
# -----------------------------------------------------------------------------
variable "log_retention_days" {
  description = "CloudWatch log group retention in days"
  type        = number
  default     = 30
}
