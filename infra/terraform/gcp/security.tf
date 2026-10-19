# ── Módulo C: Security Command Center + escaneo de vulnerabilidades ──────────
# Hallazgos de SCC (Security Health Analytics, Container Threat Detection,
# vulnerabilidades de Artifact Analysis) se publican en Pub/Sub para alertar.

resource "google_pubsub_topic" "scc_findings" {
  name   = "scc-findings"
  labels = local.labels
}

resource "google_scc_notification_config" "high_findings" {
  config_id    = "otel-lab-high-findings"
  organization = var.org_id
  description  = "Hallazgos activos HIGH/CRITICAL del proyecto otel-lab"
  pubsub_topic = google_pubsub_topic.scc_findings.id

  streaming_config {
    filter = "state = \"ACTIVE\" AND (severity = \"HIGH\" OR severity = \"CRITICAL\") AND resource.project_display_name = \"${var.project_id}\""
  }
}

# Registro de imágenes con escaneo automático de CVEs (Artifact Analysis)
resource "google_artifact_registry_repository" "images" {
  repository_id = "otel-lab"
  location      = var.region
  format        = "DOCKER"
  labels        = local.labels
  vulnerability_scanning_config {
    enablement_config = "INHERITED"
  }
}

# Golden signal de seguridad: intentos de autenticación fallidos (logs de data-service)
resource "google_logging_metric" "auth_failures" {
  name   = "data_service_auth_failures"
  filter = "resource.type=\"k8s_container\" labels.k8s-pod/app=\"data-service\" jsonPayload.message=\"Autenticación fallida\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    labels {
      key        = "reason"
      value_type = "STRING"
    }
  }
  label_extractors = { reason = "EXTRACT(jsonPayload.reason)" }
}

resource "google_monitoring_alert_policy" "auth_failures" {
  display_name = "data-service: posible fuerza bruta (> 10 auth fallidas/min)"
  combiner     = "OR"
  conditions {
    display_name = "auth failures > 10/min"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.auth_failures.name}\" AND resource.type=\"k8s_container\""
      comparison      = "COMPARISON_GT"
      threshold_value = 10
      duration        = "60s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.oncall.id]
}
