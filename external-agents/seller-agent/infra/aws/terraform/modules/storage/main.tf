# =============================================================================
# Storage module: Aurora Serverless v2 PostgreSQL + ElastiCache Redis
# =============================================================================

# -----------------------------------------------------------------------------
# Aurora Serverless v2 PostgreSQL
# -----------------------------------------------------------------------------

resource "aws_db_subnet_group" "aurora" {
  name        = "${var.name_prefix}-aurora"
  description = "Subnet group for Aurora cluster"
  subnet_ids  = var.private_subnet_ids

  tags = {
    Name = "${var.name_prefix}-aurora-subnet-group"
  }
}

resource "aws_rds_cluster" "main" {
  cluster_identifier = "${var.name_prefix}-aurora"
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned"
  engine_version     = "16.4"

  database_name   = var.db_name
  master_username = var.db_master_username
  # AWS will auto-generate and store the password in Secrets Manager
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.aurora.name
  vpc_security_group_ids = [var.aurora_security_group_id]

  serverlessv2_scaling_configuration {
    min_capacity = var.aurora_min_capacity
    max_capacity = var.aurora_max_capacity
  }

  storage_encrypted       = true
  backup_retention_period = 7
  preferred_backup_window = "03:00-04:00"
  skip_final_snapshot     = var.environment != "prod"
  final_snapshot_identifier = var.environment == "prod" ? "${var.name_prefix}-aurora-final" : null
  deletion_protection     = var.environment == "prod"

  tags = {
    Name = "${var.name_prefix}-aurora"
  }
}

resource "aws_rds_cluster_instance" "main" {
  count = 1

  identifier         = "${var.name_prefix}-aurora-${count.index}"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version

  publicly_accessible = false

  tags = {
    Name = "${var.name_prefix}-aurora-instance-${count.index}"
  }
}

# Store the database connection info in SSM for application consumption
resource "aws_ssm_parameter" "database_url" {
  name        = "/${var.name_prefix}/database-host"
  description = "Aurora cluster writer endpoint"
  type        = "String"
  value       = aws_rds_cluster.main.endpoint

  tags = {
    Name = "${var.name_prefix}-db-host"
  }
}

# -----------------------------------------------------------------------------
# ElastiCache Redis
# -----------------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "redis" {
  name        = "${var.name_prefix}-redis"
  description = "Subnet group for Redis"
  subnet_ids  = var.private_subnet_ids

  tags = {
    Name = "${var.name_prefix}-redis-subnet-group"
  }
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${var.name_prefix}-redis"
  description          = "Redis cluster for ${var.name_prefix}"

  engine               = "redis"
  engine_version       = "7.1"
  node_type            = var.redis_node_type
  num_cache_clusters   = 1
  port                 = 6379
  parameter_group_name = "default.redis7"

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [var.redis_security_group_id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = false # Set true if app supports TLS to Redis
  automatic_failover_enabled = false # Single node for dev; set true with 2+ nodes

  snapshot_retention_limit = var.environment == "prod" ? 7 : 0

  tags = {
    Name = "${var.name_prefix}-redis"
  }
}

resource "aws_ssm_parameter" "redis_endpoint" {
  name        = "/${var.name_prefix}/redis-endpoint"
  description = "ElastiCache Redis primary endpoint"
  type        = "String"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address

  tags = {
    Name = "${var.name_prefix}-redis-endpoint"
  }
}
