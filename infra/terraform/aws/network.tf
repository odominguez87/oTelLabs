# ── Módulo C: VPC con Flow Logs → CloudWatch + alertas de tráfico anómalo ────
resource "aws_vpc" "lab" {
  cidr_block           = "10.40.0.0/16"
  enable_dns_hostnames = true
  tags                 = { Name = "otel-lab-vpc" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.lab.id
  cidr_block        = cidrsubnet(aws_vpc.lab.cidr_block, 4, count.index)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = { Name = "otel-lab-private-${count.index}" }
}

resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/otel-lab/vpc-flow-logs"
  retention_in_days = 14
}

resource "aws_iam_role" "flow_logs" {
  name = "otel-lab-vpc-flow-logs"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "vpc-flow-logs.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "flow_logs" {
  role = aws_iam_role.flow_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogGroups", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.flow_logs.arn}:*"
    }]
  })
}

# Flow Logs de TODO el tráfico (ACCEPT y REJECT), agregación de 1 min
resource "aws_flow_log" "vpc" {
  vpc_id                   = aws_vpc.lab.id
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow_logs.arn
  iam_role_arn             = aws_iam_role.flow_logs.arn
  max_aggregation_interval = 60
  log_format               = "$${version} $${account-id} $${interface-id} $${srcaddr} $${dstaddr} $${srcport} $${dstport} $${protocol} $${packets} $${bytes} $${start} $${end} $${action} $${log-status} $${flow-direction} $${traffic-path}"
}

# Conexiones RECHAZADAS hacia el puerto de data-service (escaneo / acceso no autorizado)
resource "aws_cloudwatch_log_metric_filter" "rejected_to_data_service" {
  name           = "rejected-to-data-service"
  log_group_name = aws_cloudwatch_log_group.flow_logs.name
  pattern        = "[version, account, eni, src, dst, srcport, dstport=8002, protocol, packets, bytes, start, end, action=REJECT, status, direction, path]"
  metric_transformation {
    name      = "RejectedFlowsToDataService"
    namespace = "OtelLab/Network"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "rejected_flows" {
  alarm_name          = "otel-lab-rejected-flows-data-service"
  alarm_description   = "Tráfico E-W anómalo: conexiones rechazadas hacia data-service"
  namespace           = "OtelLab/Network"
  metric_name         = "RejectedFlowsToDataService"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 20
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.oncall.arn]
}

# Volumen de bytes E-W: banda de anomalía (CloudWatch Anomaly Detection, 2σ)
resource "aws_cloudwatch_log_metric_filter" "east_west_bytes" {
  name           = "east-west-bytes"
  log_group_name = aws_cloudwatch_log_group.flow_logs.name
  pattern        = "[version, account, eni, src=10.40.*, dst=10.40.*, srcport, dstport, protocol, packets, bytes, start, end, action=ACCEPT, status, direction, path]"
  metric_transformation {
    name      = "EastWestBytes"
    namespace = "OtelLab/Network"
    value     = "$bytes"
  }
}

resource "aws_cloudwatch_metric_alarm" "east_west_anomaly" {
  alarm_name          = "otel-lab-east-west-traffic-anomaly"
  alarm_description   = "Volumen E-W fuera de la banda esperada (anomaly detection)"
  comparison_operator = "GreaterThanUpperThreshold"
  evaluation_periods  = 3
  threshold_metric_id = "band"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.oncall.arn]

  metric_query {
    id          = "band"
    expression  = "ANOMALY_DETECTION_BAND(m1, 2)"
    label       = "EastWestBytes (esperado)"
    return_data = true
  }
  metric_query {
    id          = "m1"
    return_data = true
    metric {
      namespace   = "OtelLab/Network"
      metric_name = "EastWestBytes"
      period      = 300
      stat        = "Sum"
    }
  }
}

resource "aws_security_group" "data_service" {
  name        = "otel-lab-data-service"
  description = "data-service: solo acepta tráfico del mesh (service-a)"
  vpc_id      = aws_vpc.lab.id
}

resource "aws_security_group" "rds" {
  name        = "otel-lab-rds"
  description = "RDS: solo desde data-service"
  vpc_id      = aws_vpc.lab.id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.data_service.id]
  }
}
