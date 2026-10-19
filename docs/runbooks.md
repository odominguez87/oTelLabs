# Runbooks de alertas

Cada alerta llega enriquecida por `alert-enricher` con `trace_id`, enlace a la traza (Jaeger) y a los logs (Loki).
Consultar: `curl localhost:8080/alerts`.

## data-service-anomaly
**Alerta:** `DataServiceAnomalyCorrelated` — error rate sobre μ+2σ **y** p99 > 300 ms.

1. Abrir `trace_url` de la alerta: ¿qué span falla? `SELECT pricing` (Cloud SQL) o `SELECT reviews` (RDS) → problema de BD; `chaos.injected=true` → experimento en curso.
2. Dashboard **Lab 4 — AIOps y SLOs**: comparar `db.client.operation.duration` de `cloudsql-pg` vs `rds-pg` para aislar la BD.
3. Logs: `logs_url` (Loki, filtrado por `trace_id`) → `error.type`, `db.response.status_code`.
4. Mitigación: si es una sola BD, degradar la respuesta (service-a ya responde `degraded: true`); si es chaos, `POST /admin/chaos {"error_rate":0}`.

## service-a-latency
**Alerta:** `ServiceALatencySLOBreach` — p95 de service-a > 500 ms durante 30 s.

1. En la traza, comparar duración de `call.service-b.inventory` (cliente) vs `GET /inventory` (servidor de service-b): si la diferencia es grande → red, no aplicación.
2. Revisar `tc qdisc show` en service-b (experimentos de red) y el panel *E-W por código de respuesta* (Envoy).
3. Mitigación: retirar la inyección de forma **gradual** (ver Lab 3: el rollback abrupto provoca errores).

## security
- `AuthFailuresSpike`: > 10 autenticaciones fallidas/min → revisar `client_ip` en logs (`{service_name="data-service"} |= "Autenticación fallida"`), bloquear origen.
- `UnmeshedEastWestTraffic`: tráfico a data-service sin pasar por Envoy → identificar el origen (log *"Request recibido fuera del service mesh"*) y cerrar el puerto 8002 (en nube: security group / firewall, ver `infra/terraform`).
- `EastWestTrafficAnomaly`: volumen E-W > μ+3σ → posible exfiltración o bucle de reintentos.
