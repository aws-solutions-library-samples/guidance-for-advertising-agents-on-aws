variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for DB and cache subnet groups"
  type        = list(string)
}

variable "aurora_security_group_id" {
  description = "Security group ID to attach to the Aurora cluster"
  type        = string
}

variable "redis_security_group_id" {
  description = "Security group ID to attach to the Redis cluster"
  type        = string
}

variable "db_name" {
  description = "Name of the PostgreSQL database"
  type        = string
}

variable "db_master_username" {
  description = "Master username for Aurora"
  type        = string
}

variable "aurora_min_capacity" {
  description = "Aurora Serverless v2 minimum ACU"
  type        = number
}

variable "aurora_max_capacity" {
  description = "Aurora Serverless v2 maximum ACU"
  type        = number
}

variable "redis_node_type" {
  description = "ElastiCache Redis node type"
  type        = string
}
