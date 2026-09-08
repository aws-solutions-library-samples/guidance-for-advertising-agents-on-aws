terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Uncomment and configure for remote state
  # backend "s3" {
  #   bucket         = "ad-seller-terraform-state"
  #   key            = "ad-seller-system/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "ad-seller-system"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

locals {
  name_prefix = "ad-seller-${var.environment}"
}

# -----------------------------------------------------------------------------
# Network module: VPC, subnets, NAT, IGW, route tables, security groups
# -----------------------------------------------------------------------------
module "network" {
  source = "./modules/network"

  name_prefix        = local.name_prefix
  environment        = var.environment
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
  container_port     = var.container_port
}

# -----------------------------------------------------------------------------
# Storage module: Aurora Serverless v2 PostgreSQL, ElastiCache Redis
# -----------------------------------------------------------------------------
module "storage" {
  source = "./modules/storage"

  name_prefix          = local.name_prefix
  environment          = var.environment
  vpc_id               = module.network.vpc_id
  private_subnet_ids   = module.network.private_subnet_ids
  aurora_security_group_id = module.network.aurora_security_group_id
  redis_security_group_id  = module.network.redis_security_group_id

  db_name              = var.db_name
  db_master_username   = var.db_master_username
  aurora_min_capacity  = var.aurora_min_capacity
  aurora_max_capacity  = var.aurora_max_capacity
  redis_node_type      = var.redis_node_type
}

# -----------------------------------------------------------------------------
# Compute module: ECS Fargate, ALB, CloudWatch, IAM, SSM references
# -----------------------------------------------------------------------------
module "compute" {
  source = "./modules/compute"

  name_prefix        = local.name_prefix
  environment        = var.environment
  region             = var.region
  vpc_id             = module.network.vpc_id
  public_subnet_ids  = module.network.public_subnet_ids
  private_subnet_ids = module.network.private_subnet_ids
  alb_security_group_id = module.network.alb_security_group_id
  ecs_security_group_id = module.network.ecs_security_group_id

  container_image_tag  = var.container_image_tag
  container_port       = var.container_port
  desired_count        = var.desired_count
  cpu                  = var.task_cpu
  memory               = var.task_memory
  health_check_path    = var.health_check_path

  database_endpoint    = module.storage.aurora_cluster_endpoint
  database_port        = module.storage.aurora_cluster_port
  database_name        = var.db_name
  database_username    = var.db_master_username
  redis_endpoint       = module.storage.redis_endpoint
  redis_port           = module.storage.redis_port

  certificate_arn      = var.certificate_arn
  log_retention_days   = var.log_retention_days
}
