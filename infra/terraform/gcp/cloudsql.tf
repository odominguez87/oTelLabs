# ── Módulo A: Cloud SQL for PostgreSQL (base "catalog" de data-service) ──────
resource "google_sql_database_instance" "catalog" {
  name             = "otel-lab-catalog"
  database_version = "POSTGRES_16"
  region           = var.region
  depends_on       = [google_service_networking_connection.sql]

  settings {
    tier              = "db-custom-1-3840"
    availability_type = "ZONAL"
    user_labels       = local.labels

    ip_configuration {
      ipv4_enabled    = false # solo IP privada
      private_network = google_compute_network.vpc.id
    }

    # Query Insights: latencia por consulta, correlacionable con trazas (sqlcommenter)
    insights_config {
      query_insights_enabled  = true
      query_string_length     = 1024
      record_application_tags = true
      record_client_address   = false
    }

    database_flags {
      name  = "log_min_duration_statement"
      value = "200" # registra consultas > 200 ms
    }

    backup_configuration { enabled = true }
  }
  deletion_protection = false
}

resource "google_sql_database" "catalog" {
  name     = "catalog"
  instance = google_sql_database_instance.catalog.name
}

resource "google_sql_user" "app" {
  name     = "app"
  instance = google_sql_database_instance.catalog.name
  password = var.db_password
}
