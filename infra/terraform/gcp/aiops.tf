# ── Módulo B: detección de anomalías en Cloud Monitoring ─────────────────────
# Las métricas OTel llegan a Managed Service for Prometheus, así que se reutiliza
# la misma lógica PromQL validada en local (prometheus/rules/aiops.yml).
# Lecciones del experimento local aplicadas aquí:
#   • el baseline se aprende solo de periodos con el SLO de latencia cumplido
#     (evita la contaminación del baseline por incidentes recientes);
#   • en producción conviene una ventana mayor (p. ej. misma hora de la semana
#     anterior) para capturar estacionalidad.

resource "google_monitoring_alert_policy" "data_service_anomaly" {
  display_name = "data-service: anomalía correlacionada (error > μ+2σ Y p99 > SLO)"
  combiner     = "OR"
  severity     = "CRITICAL"

  conditions {
    display_name = "error_ratio > baseline + 2σ AND latency_p99 > 300ms"
    condition_prometheus_query_language {
      query               = <<-EOT
        (
          sum(rate(data_service_requests_total{route="/product",status_code=~"5.."}[1m]))
            / sum(rate(data_service_requests_total{route="/product"}[1m]))
          >
          avg_over_time(((sum(rate(data_service_requests_total{route="/product",status_code=~"5.."}[1m]))
            / sum(rate(data_service_requests_total{route="/product"}[1m])))
            unless on() (histogram_quantile(0.99, sum(rate(data_service_request_duration_seconds_bucket{route="/product"}[1m])) by (le)) > 0.3))[15m:1m] offset 2m)
          + 2 * clamp_min(stddev_over_time(((sum(rate(data_service_requests_total{route="/product",status_code=~"5.."}[1m]))
            / sum(rate(data_service_requests_total{route="/product"}[1m])))
            unless on() (histogram_quantile(0.99, sum(rate(data_service_request_duration_seconds_bucket{route="/product"}[1m])) by (le)) > 0.3))[15m:1m] offset 2m), 0.005)
        )
        and on()
        (histogram_quantile(0.99, sum(rate(data_service_request_duration_seconds_bucket{route="/product"}[1m])) by (le)) > 0.3)
      EOT
      duration            = "60s"
      evaluation_interval = "30s"
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    mime_type = "text/markdown"
    content   = "Ver traza del request fallido en Cloud Trace (filtro service.name=data-service, status=ERROR). Runbook: docs/runbooks.md#data-service-anomaly"
  }
  notification_channels = [google_monitoring_notification_channel.oncall.id]
  user_labels           = local.labels
}

# SLO de disponibilidad de data-service con alerta de burn rate (error budget)
resource "google_monitoring_custom_service" "data_service" {
  service_id   = "data-service"
  display_name = "data-service"
}

resource "google_monitoring_slo" "data_service_availability" {
  service             = google_monitoring_custom_service.data_service.service_id
  slo_id              = "availability-995"
  display_name        = "99.5% de requests exitosos (28 días)"
  goal                = 0.995
  rolling_period_days = 28

  request_based_sli {
    good_total_ratio {
      good_service_filter  = "metric.type=\"prometheus.googleapis.com/data_service_requests_total/counter\" metric.label.status_code=monitoring.regex.full_match(\"[23]..\")"
      total_service_filter = "metric.type=\"prometheus.googleapis.com/data_service_requests_total/counter\""
    }
  }
}

resource "google_monitoring_alert_policy" "data_service_burn_rate" {
  display_name = "data-service: burn rate del error budget > 14.4 (1h)"
  combiner     = "OR"
  conditions {
    display_name = "Fast burn"
    condition_threshold {
      filter          = "select_slo_burn_rate(\"${google_monitoring_slo.data_service_availability.id}\", \"3600s\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 14.4
      duration        = "0s"
    }
  }
  notification_channels = [google_monitoring_notification_channel.oncall.id]
}
