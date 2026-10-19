# Laboratorio 4 — Arquitectura observable completa

Extiende el stack de los labs 2 y 3 con un tercer microservicio, service mesh, AIOps,
observabilidad de red/seguridad y chaos engineering. Todo se ejecuta en local con
Docker; los servicios gestionados de nube quedan como **IaC validada sin desplegar**
(`infra/terraform/{gcp,aws}`, `terraform validate` OK, sin costos).

## Arquitectura

```
k6 / clientes (N-S)
      │
      ▼
 service-a :8000 ──────────► service-b :8001 ──► postgres (orders/inventory)
      │   (E-W, W3C traceparent)
      ▼
 envoy-data :10000  ◄── sidecar del service mesh (L7: métricas, spans, access log, timeout/retry)
      │  x-mesh-sidecar: envoy
      ▼
 data-service :8002 ──► cloudsql-pg  (GCP Cloud SQL · base "catalog", tabla pricing)
                    └─► rds-pg       (AWS RDS · base "reviews", tabla reviews)

Telemetría:  servicios + Envoy ──OTLP──► OTel Collector ─┬─► Jaeger           (trazas · tail sampling: errores + lentas + 10 %)
                                                         ├─► Prometheus       (métricas) ─► Alertmanager ─► alert-enricher (+trace_id)
                                                         └─► Loki             (logs con trace_id)
Seguridad:   Trivy ─► security/reports ─► cve-exporter ─► Prometheus
Visualización: Grafana (dashboards Lab 4 — AIOps y SLOs · Golden Signals de Seguridad)
```

## Equivalencias local ↔ nube

| Requisito | Local (ejecutado) | GCP (IaC) | AWS (IaC) |
|---|---|---|---|
| BD gestionada | `cloudsql-pg`, `rds-pg` | `cloudsql.tf` (Cloud SQL PG16, Query Insights) | `rds.tf` (RDS PG16, Performance Insights) |
| Service mesh L7 | Envoy sidecar (`mesh/`) | `gke_mesh.tf` (Cloud Service Mesh) | `appmesh.tf` (App Mesh) |
| Detección de anomalías | Reglas μ+2σ correlacionadas (`prometheus/rules/aiops.yml`) | `aiops.tf` (misma PromQL en Cloud Monitoring + SLO/burn rate) | `aiops.tf` (DevOps Guru + Anomaly Detection + alarma compuesta) |
| Alerta enriquecida | `tools/alert_enricher.py` | Notification channel → función | SNS → Lambda |
| Flow logs + alerta | Métricas E-W de Envoy + `UnmeshedEastWestTraffic` | `network.tf` (VPC Flow Logs + log metric + alerta) | `network.tf` (Flow Logs → CloudWatch + metric filter + anomaly band) |
| Seguridad | Trivy + `cve-exporter`, auth fallidas | `security.tf` (SCC + Artifact Analysis) | `security.tf` (Security Hub + GuardDuty + Inspector) |

## Ejecutar

```bash
docker compose up -d --build
./security/scan_images.sh                                   # CVEs → dashboard de seguridad

# tráfico de fondo (necesario para el baseline de la regla AIOps, ≥ 15 min)
k6 run --env RATE=10 --env DURATION=90m chaos/k6_chaos.js &

python3 lab4/experiments.py noise   # Módulo B: ruido vs incidente (estáticas vs AIOps)
python3 lab4/experiments.py d1      # Módulo D: +200 ms en service-b
python3 lab4/experiments.py d2      # Módulo D: 10 % errores en data-service
./lab4/security_demo.sh             # Módulo C: fuerza bruta + bypass del mesh

curl localhost:8080/alerts          # alertas enriquecidas con trace_id
```

Resultados en `lab4/results/`. Validar la IaC:

```bash
docker run --rm -v "$PWD/infra/terraform/gcp:/w" -w /w hashicorp/terraform:1.9.8 init -backend=false
docker run --rm -v "$PWD/infra/terraform/gcp:/w" -w /w hashicorp/terraform:1.9.8 validate
```

## Accesos

| UI | URL |
|---|---|
| Grafana (dashboards `Lab 4 — …`) | http://localhost:3000 |
| Jaeger | http://localhost:16686 |
| Prometheus (reglas / alertas) | http://localhost:9091/alerts |
| Alertmanager | http://localhost:9093 |
| Alertas enriquecidas | http://localhost:8080/alerts |
| Envoy admin / stats L7 | http://localhost:9901/stats/prometheus |
