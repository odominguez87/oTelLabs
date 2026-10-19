# ════════════════════════════════════════════════════════════════════════════
# GCP — Arquitectura observable (Lab 4). Validado con `terraform validate`;
# NO aplicado (sin costos). Equivalencias con el entorno local en README.md.
# ════════════════════════════════════════════════════════════════════════════
terraform {
  required_version = ">= 1.6"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  type        = string
  description = "Proyecto GCP"
  default     = "otel-lab-project"
}
variable "org_id" {
  type        = string
  description = "Organización GCP (Security Command Center es un servicio a nivel de organización)"
  default     = "000000000000"
}
variable "region" {
  type    = string
  default = "us-central1"
}
variable "alert_email" {
  type    = string
  default = "sre-oncall@example.com"
}
variable "db_password" {
  type      = string
  sensitive = true
  default   = "change-me-in-secret-manager"
}

locals {
  labels = { app = "otel-lab", env = "dev", owner = "grupo-7" }
}

resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com", "container.googleapis.com", "sqladmin.googleapis.com",
    "servicenetworking.googleapis.com", "monitoring.googleapis.com", "logging.googleapis.com",
    "cloudtrace.googleapis.com", "mesh.googleapis.com", "gkehub.googleapis.com",
    "securitycenter.googleapis.com", "containerscanning.googleapis.com", "artifactregistry.googleapis.com",
    "pubsub.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_monitoring_notification_channel" "oncall" {
  display_name = "SRE on-call"
  type         = "email"
  labels       = { email_address = var.alert_email }
}
