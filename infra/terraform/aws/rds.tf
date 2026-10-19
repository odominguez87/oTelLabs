# ── Módulo A: RDS for PostgreSQL (base "reviews" de data-service) ────────────
resource "aws_db_subnet_group" "lab" {
  name       = "otel-lab"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "reviews" {
  identifier             = "otel-lab-reviews"
  engine                 = "postgres"
  engine_version         = "16.4"
  instance_class         = "db.t4g.micro"
  allocated_storage      = 20
  db_name                = "reviews"
  username               = "app"
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.lab.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  storage_encrypted      = true
  skip_final_snapshot    = true

  # Observabilidad de BD: Performance Insights + Enhanced Monitoring + logs
  performance_insights_enabled          = true
  performance_insights_retention_period = 7
  monitoring_interval                   = 15
  monitoring_role_arn                   = aws_iam_role.rds_monitoring.arn
  enabled_cloudwatch_logs_exports       = ["postgresql"]
  parameter_group_name                  = aws_db_parameter_group.pg.name
}

resource "aws_db_parameter_group" "pg" {
  name   = "otel-lab-pg16"
  family = "postgres16"
  parameter {
    name  = "log_min_duration_statement"
    value = "200"
  }
}

resource "aws_iam_role" "rds_monitoring" {
  name = "otel-lab-rds-monitoring"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "monitoring.rds.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}
