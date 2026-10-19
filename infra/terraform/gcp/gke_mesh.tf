# ── Módulo A: GKE + Cloud Service Mesh (Envoy gestionado, observabilidad L7) ──
resource "google_container_cluster" "lab" {
  name                     = "otel-lab"
  location                 = var.region
  network                  = google_compute_network.vpc.id
  subnetwork               = google_compute_subnetwork.services.id
  remove_default_node_pool = true
  initial_node_count       = 1
  deletion_protection      = false
  resource_labels          = merge(local.labels, { mesh_id = "proj-${data.google_project.this.number}" })

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Managed Prometheus + métricas de sistema/workloads hacia Cloud Monitoring
  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS", "APISERVER", "DEPLOYMENT", "POD"]
    managed_prometheus { enabled = true }
  }
  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS"]
  }

  fleet {
    project = var.project_id
  }
}

resource "google_container_node_pool" "default" {
  name       = "default"
  cluster    = google_container_cluster.lab.id
  node_count = 2
  node_config {
    machine_type = "e2-standard-4"
    labels       = local.labels
    workload_metadata_config { mode = "GKE_METADATA" }
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
}

data "google_project" "this" {}

# Cloud Service Mesh gestionado para la flota: inyecta sidecars Envoy y exporta
# métricas L7 (request_count, latencias, códigos) y topología a Cloud Monitoring
resource "google_gke_hub_feature" "servicemesh" {
  name     = "servicemesh"
  location = "global"
}

resource "google_gke_hub_feature_membership" "servicemesh" {
  location   = "global"
  feature    = google_gke_hub_feature.servicemesh.name
  membership = google_container_cluster.lab.fleet[0].membership_id
  mesh {
    management = "MANAGEMENT_AUTOMATIC"
  }
}
