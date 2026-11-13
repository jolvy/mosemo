resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = [for subnet in aws_subnet.private : subnet.id]
}

resource "aws_security_group" "database" {
  name        = "${local.name}-database"
  description = "PostgreSQL access from EKS nodes and pods."
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "database_from_nodes" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_db_instance" "main" {
  identifier             = local.name
  engine                 = "postgres"
  engine_version         = "18.3"
  instance_class         = "db.t4g.micro"
  allocated_storage      = 20
  storage_type           = "gp3"
  storage_encrypted      = true
  db_name                = "mosemo"
  username               = "mosemo"
  password_wo            = var.db_password
  password_wo_version    = 1
  port                   = 5432
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]
  multi_az               = false
  publicly_accessible    = false
  # Single-AZ in 2a with Kafka and the loadgen node, so measured DB round trips never cross AZs.
  availability_zone = aws_subnet.private["a"].availability_zone

  # Production settings; session-down.sh lifts protection and skips the snapshot explicitly.
  # Retention is 1 day only because the AWS Free plan rejects longer periods (FreeTierRestrictionError);
  # raise it (production: 7) when the account leaves the Free plan.
  backup_retention_period   = 1
  deletion_protection       = true
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = "${local.name}-final"
  delete_automated_backups  = false
}
