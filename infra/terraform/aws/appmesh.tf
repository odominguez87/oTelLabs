# ── Módulo A: AWS App Mesh (Envoy) — observabilidad L7 entre servicios ───────
# Nota: AWS anunció el fin de soporte de App Mesh (sep-2026); la ruta de migración
# es Amazon ECS Service Connect / VPC Lattice. Se mantiene por requisito del lab.
resource "aws_appmesh_mesh" "lab" {
  name = "otel-lab"
  spec {
    egress_filter { type = "DROP_ALL" } # zero-trust: solo destinos declarados
  }
}

resource "aws_service_discovery_private_dns_namespace" "lab" {
  name = "otel-lab.local"
  vpc  = aws_vpc.lab.id
}

resource "aws_appmesh_virtual_node" "data_service" {
  name      = "data-service"
  mesh_name = aws_appmesh_mesh.lab.name
  spec {
    listener {
      port_mapping {
        port     = 8002
        protocol = "http"
      }
      timeout {
        http {
          per_request {
            unit  = "ms"
            value = 1000
          }
        }
      }
      outlier_detection {
        max_ejection_percent = 50
        max_server_errors    = 5
        base_ejection_duration {
          unit  = "s"
          value = 30
        }
        interval {
          unit  = "s"
          value = 10
        }
      }
    }
    service_discovery {
      aws_cloud_map {
        namespace_name = aws_service_discovery_private_dns_namespace.lab.name
        service_name   = "data-service"
      }
    }
    # Access log del Envoy (equivalente al access log JSON local)
    logging {
      access_log {
        file { path = "/dev/stdout" }
      }
    }
  }
}

resource "aws_appmesh_virtual_service" "data_service" {
  name      = "data-service.otel-lab.local"
  mesh_name = aws_appmesh_mesh.lab.name
  spec {
    provider {
      virtual_node { virtual_node_name = aws_appmesh_virtual_node.data_service.name }
    }
  }
}

resource "aws_appmesh_virtual_node" "service_a" {
  name      = "service-a"
  mesh_name = aws_appmesh_mesh.lab.name
  spec {
    listener {
      port_mapping {
        port     = 8000
        protocol = "http"
      }
    }
    # service-a solo puede hablar con data-service (y lo que se declare aquí)
    backend {
      virtual_service {
        virtual_service_name = aws_appmesh_virtual_service.data_service.name
        client_policy {
          tls {
            enforce = false
            validation {
              trust {
                file { certificate_chain = "/etc/ssl/certs/ca-bundle.crt" }
              }
            }
          }
        }
      }
    }
    service_discovery {
      aws_cloud_map {
        namespace_name = aws_service_discovery_private_dns_namespace.lab.name
        service_name   = "service-a"
      }
    }
    logging {
      access_log {
        file { path = "/dev/stdout" }
      }
    }
  }
}
