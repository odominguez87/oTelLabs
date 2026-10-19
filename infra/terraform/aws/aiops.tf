# ── Módulo B: Amazon DevOps Guru + CloudWatch Anomaly Detection ──────────────
# DevOps Guru analiza con ML las métricas/logs de los recursos etiquetados
# (RDS, ECS, Lambda…) y genera "insights" reactivos/proactivos.
resource "aws_devopsguru_resource_collection" "lab" {
  type = "AWS_TAGS"
  tags {
    app_boundary_key = "DevOps-Guru-otel-lab"
    tag_values       = ["data-service"]
  }
}

resource "aws_devopsguru_notification_channel" "oncall" {
  sns {
    topic_arn = aws_sns_topic.oncall.arn
  }
  filters {
    severities    = ["HIGH", "MEDIUM"]
    message_types = ["NEW_INSIGHT", "SEVERITY_UPGRADED"]
  }
}

# Regla de correlación (misma lógica que prometheus/rules/aiops.yml):
#   error_rate fuera de la banda de anomalía (2σ)  Y  p99 > SLO 300 ms
# Las métricas llegan por ADOT/OTel Collector (exportador awsemf) al namespace OtelLab/DataService.
resource "aws_cloudwatch_metric_alarm" "error_rate_anomaly" {
  alarm_name          = "data-service-error-rate-anomaly"
  comparison_operator = "GreaterThanUpperThreshold"
  evaluation_periods  = 2
  threshold_metric_id = "band"
  treat_missing_data  = "notBreaching"
  metric_query {
    id          = "band"
    expression  = "ANOMALY_DETECTION_BAND(err, 2)"
    return_data = true
  }
  metric_query {
    id          = "err"
    return_data = true
    metric {
      namespace   = "OtelLab/DataService"
      metric_name = "ErrorRate"
      period      = 60
      stat        = "Average"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "latency_p99_slo" {
  alarm_name          = "data-service-latency-p99-slo"
  namespace           = "OtelLab/DataService"
  metric_name         = "RequestDuration"
  extended_statistic  = "p99"
  period              = 60
  evaluation_periods  = 2
  threshold           = 0.3
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

# Alarma compuesta = correlación. Solo esta notifica (las anteriores no tienen acciones),
# eliminando las alertas ruidosas de cada umbral por separado.
resource "aws_cloudwatch_composite_alarm" "data_service_correlated" {
  alarm_name        = "data-service-anomaly-correlated"
  alarm_description = "error_rate > banda 2σ Y latency_p99 > SLO. Runbook: docs/runbooks.md#data-service-anomaly"
  alarm_rule        = "ALARM(${aws_cloudwatch_metric_alarm.error_rate_anomaly.alarm_name}) AND ALARM(${aws_cloudwatch_metric_alarm.latency_p99_slo.alarm_name})"
  alarm_actions     = [aws_sns_topic.oncall.arn]
}
