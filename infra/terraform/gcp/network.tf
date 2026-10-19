# ── Módulo C: VPC con Flow Logs + alertas de tráfico anómalo ─────────────────
resource "google_compute_network" "vpc" {
  name                    = "otel-lab-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "services" {
  name                     = "otel-lab-services"
  network                  = google_compute_network.vpc.id
  region                   = var.region
  ip_cidr_range            = "10.10.0.0/20"
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }

  # VPC Flow Logs: muestreo 50 %, agregación 30 s, con metadatos (nodos/pods GKE)
  log_config {
    aggregation_interval = "INTERVAL_30_SEC"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

# Este-Oeste permitido solo dentro de la subred de servicios
resource "google_compute_firewall" "allow_internal" {
  name      = "otel-lab-allow-internal"
  network   = google_compute_network.vpc.id
  direction = "INGRESS"
  priority  = 1000
  allow { protocol = "tcp" }
  source_ranges = ["10.10.0.0/20", "10.20.0.0/16"]
  log_config { metadata = "INCLUDE_ALL_METADATA" }
}

# Firewall Rules Logging en la regla de denegación: cada intento bloqueado queda registrado
resource "google_compute_firewall" "deny_all_ingress" {
  name      = "otel-lab-deny-all-ingress"
  network   = google_compute_network.vpc.id
  direction = "INGRESS"
  priority  = 65000
  deny { protocol = "all" }
  source_ranges = ["0.0.0.0/0"]
  log_config { metadata = "INCLUDE_ALL_METADATA" }
}

# Métrica basada en logs: conexiones hacia el puerto de data-service que NO vienen
# del rango de pods del mesh (equivalente a la alerta local UnmeshedEastWestTraffic)
resource "google_logging_metric" "unexpected_east_west" {
  name   = "vpc_flow_unexpected_east_west"
  filter = <<-EOT
    resource.type="gce_subnetwork"
    log_id("compute.googleapis.com/vpc_flows")
    jsonPayload.connection.dest_port=8002
    NOT ip_in_net(jsonPayload.connection.src_ip, "10.20.0.0/16")
  EOT
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "unexpected_east_west" {
  display_name = "Tráfico E-W no autorizado hacia data-service (VPC Flow Logs)"
  combiner     = "OR"
  conditions {
    display_name = "Flujos fuera del mesh > 0"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.unexpected_east_west.name}\" AND resource.type=\"gce_subnetwork\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "60s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.oncall.id]
  user_labels           = local.labels
}

# Peering para IP privada de Cloud SQL
resource "google_compute_global_address" "sql_private" {
  name          = "otel-lab-sql-private"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "sql" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql_private.name]
}
