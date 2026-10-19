# ── Módulo C: AWS Security Hub básico + GuardDuty + Inspector (CVEs) ─────────
resource "aws_securityhub_account" "lab" {
  enable_default_standards = false
}

resource "aws_securityhub_standards_subscription" "fsbp" {
  depends_on    = [aws_securityhub_account.lab]
  standards_arn = "arn:aws:securityhub:${var.region}::standards/aws-foundational-security-best-practices/v/1.0.0"
}

# GuardDuty: detección de amenazas sobre VPC Flow Logs, DNS y CloudTrail → Security Hub
resource "aws_guardduty_detector" "lab" {
  enable = true
}

# Inspector v2: CVEs de imágenes ECR y de instancias → hallazgos en Security Hub
resource "aws_inspector2_enabler" "lab" {
  account_ids    = [data.aws_caller_identity.current.account_id]
  resource_types = ["ECR", "EC2"]
}

resource "aws_ecr_repository" "data_service" {
  name = "otel-lab/data-service"
  image_scanning_configuration { scan_on_push = true }
}

# Hallazgos HIGH/CRITICAL de Security Hub → SNS (alerta de seguridad)
resource "aws_cloudwatch_event_rule" "securityhub_high" {
  name = "otel-lab-securityhub-high"
  event_pattern = jsonencode({
    source        = ["aws.securityhub"]
    "detail-type" = ["Security Hub Findings - Imported"]
    detail        = { findings = { Severity = { Label = ["HIGH", "CRITICAL"] }, Workflow = { Status = ["NEW"] } } }
  })
}

resource "aws_cloudwatch_event_target" "securityhub_sns" {
  rule = aws_cloudwatch_event_rule.securityhub_high.name
  arn  = aws_sns_topic.oncall.arn
}

# Golden signal: intentos de autenticación fallidos en los logs de data-service
resource "aws_cloudwatch_log_group" "data_service" {
  name              = "/otel-lab/data-service"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_metric_filter" "auth_failures" {
  name           = "data-service-auth-failures"
  log_group_name = aws_cloudwatch_log_group.data_service.name
  pattern        = "{ $.message = \"Autenticación fallida\" }"
  metric_transformation {
    name      = "AuthFailures"
    namespace = "OtelLab/Security"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "auth_failures" {
  alarm_name          = "data-service-auth-failures-spike"
  namespace           = "OtelLab/Security"
  metric_name         = "AuthFailures"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.oncall.arn]
}
