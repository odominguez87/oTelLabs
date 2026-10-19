#!/usr/bin/env python3
"""Genera los dashboards del Lab 4 (seguridad y AIOps) como JSON provisionado en Grafana."""
import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards")
DS = "Prometheus"


def ts(pid, title, targets, x, y, w=12, h=8, unit="short", thresholds=None, stack=False, desc=""):
    p = {
        "id": pid, "type": "timeseries", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 10,
                                                               "stacking": {"mode": "normal" if stack else "none"}}},
                        "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
        "targets": [{"refId": chr(65 + i), "expr": e, "legendFormat": l} for i, (e, l) in enumerate(targets)],
    }
    if thresholds:
        p["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": [
            {"color": "green", "value": None}, {"color": "red", "value": thresholds}]}
        p["fieldConfig"]["defaults"]["custom"]["thresholdsStyle"] = {"mode": "line+area"}
    return p


def stat(pid, title, expr, x, y, w=6, h=5, unit="short", red=None, desc="", legend=""):
    steps = [{"color": "green", "value": None}] + ([{"color": "red", "value": red}] if red is not None else [])
    return {
        "id": pid, "type": "stat", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["max"]}, "colorMode": "background", "graphMode": "area",
                    "textMode": "value"},
        "targets": [{"refId": "A", "expr": expr, "legendFormat": legend}],
    }


def bars(pid, title, expr, legend, x, y, w=12, h=8, desc=""):
    return {
        "id": pid, "type": "bargauge", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [
            {"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 10}]}},
            "overrides": []},
        "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                    "reduceOptions": {"calcs": ["lastNotNull"]}},
        "targets": [{"refId": "A", "expr": expr, "legendFormat": legend, "instant": True}],
    }


def logs(pid, title, expr, x, y, w=24, h=9, desc=""):
    return {
        "id": pid, "type": "logs", "title": title, "description": desc,
        "datasource": {"type": "loki", "uid": "loki"},
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True,
                    "sortOrder": "Descending", "showLabels": False, "dedupStrategy": "none"},
        "targets": [{"refId": "A", "expr": expr, "datasource": {"type": "loki", "uid": "loki"}}],
    }


def dashboard(uid, title, panels, desc):
    return {"uid": uid, "title": title, "description": desc, "tags": ["lab4"], "timezone": "browser",
            "schemaVersion": 39, "refresh": "10s", "time": {"from": "now-30m", "to": "now"}, "panels": panels}


security = dashboard("lab4-security", "Lab 4 — Golden Signals de Seguridad", [
    stat(1, "Auth fallidas / min (máx. en la ventana)", "security:auth_failures:rate1m", 0, 0, red=10,
         desc="Intentos de autenticación fallidos en data-service (API key ausente o inválida)"),
    stat(2, "Tráfico fuera del mesh (rps, máx.)", 'sum(rate(otelcol_data_service_requests_total{via_mesh="false"}[1m])) or vector(0)',
         6, 0, red=0.0001, desc="Requests que llegan a data-service sin pasar por su sidecar Envoy (bypass)"),
    stat(3, "CVEs CRITICAL activos", 'sum(cve_active{severity="CRITICAL"})', 12, 0, red=1, desc="Trivy, todas las imágenes"),
    stat(4, "Alertas de seguridad (máx. simultáneas)", 'count(ALERTS{kind="security",alertstate="firing"}) or vector(0)', 18, 0, red=1),
    ts(5, "Intentos de autenticación fallidos por motivo (por minuto)",
       [('sum by (reason)(rate(otelcol_data_service_auth_failures_total[1m])) * 60', "{{reason}}")], 0, 5, stack=True),
    ts(6, "Tráfico Norte-Sur vs Este-Oeste (rps)",
       [("network:north_south_rps", "N-S: clientes → service-a"),
        ("network:east_west_rps", "E-W: service-a → data-service (mesh)"),
        ('sum(rate(otelcol_data_service_requests_total{via_mesh="false"}[1m]))', "E-W fuera del mesh")], 12, 5,
       desc="N-S desde las métricas de service-a; E-W desde el sidecar Envoy (L7)"),
    ts(7, "E-W por código de respuesta (Envoy L7)",
       [('sum by (envoy_response_code)(rate(envoy_cluster_upstream_rq{envoy_cluster_name="data_service"}[1m]))',
         "HTTP {{envoy_response_code}}")], 0, 13, stack=True),
    bars(8, "CVEs activos por severidad (todas las imágenes)",
         'sum by (severity)(cve_active{severity=~"CRITICAL|HIGH|MEDIUM|LOW"})', "{{severity}}", 12, 13),
    bars(9, "CVEs CRITICAL + HIGH por imagen", 'sum by (image)(cve_active{severity=~"CRITICAL|HIGH"})',
         "{{image}}", 0, 21),
    bars(10, "CVEs con versión corregida disponible (accionables)", 'sum by (image)(cve_fixable{severity=~"CRITICAL|HIGH"})',
         "{{image}}", 12, 21),
    logs(11, "Logs de seguridad de data-service (Loki · OTLP, con trace_id)",
         '{service_name="data-service"} |~ "Autenticación fallida|fuera del service mesh" | line_format "{{.severity_text}} trace_id={{.trace_id}} {{__line__}}"',
         0, 29),
], "Módulo C: autenticación fallida, tráfico N-S/E-W y CVEs activos")

aiops = dashboard("lab4-aiops", "Lab 4 — AIOps y SLOs (data-service)", [
    ts(1, "Error rate de data-service vs banda dinámica (μ + 2σ)",
       [("data_service:error_ratio:rate1m", "error rate"),
        ("data_service:error_ratio:upper_band", "umbral dinámico μ+2σ"),
        ("data_service:error_ratio:baseline_avg", "baseline μ (15 min)"),
        ("vector(0.01)", "umbral estático 1 %")], 0, 0, unit="percentunit"),
    ts(2, "Latencia p99 de data-service vs SLO (300 ms)",
       [("data_service:latency_p99:rate1m", "p99")], 12, 0, unit="s", thresholds=0.3),
    ts(3, "Notificaciones de alerta acumuladas: estáticas vs AIOps",
       [('sum by (kind)(enriched_alerts_total{kind=~"static|aiops"})', "{{kind}}")], 0, 8,
       desc="Cada punto = notificaciones 'firing' recibidas por el alert-enricher"),
    ts(4, "Alertas activas (Prometheus ALERTS)",
       [('sum by (alertname)(ALERTS{alertstate="firing"})', "{{alertname}}")], 12, 8, stack=True),
    ts(5, "p95 de service-a vs SLO (500 ms)",
       [("service_a:latency_p95:rate1m", "p95 service-a")], 0, 16, unit="s", thresholds=0.5),
    ts(6, "Burn rate del error budget de data-service (SLO 99,5 %)",
       [("data_service:burn_rate:1m", "burn rate (1m)")], 12, 16, thresholds=14.4),
    ts(7, "db.client.operation.duration p95 — Cloud SQL vs RDS",
       [('histogram_quantile(0.95, sum by (le, server_address, db_collection_name)'
         '(rate(otelcol_db_client_operation_duration_seconds_bucket[1m])))', "{{server_address}} · {{db_collection_name}}")],
       0, 24, unit="s", desc="Métrica estándar de las OTel DB Semantic Conventions"),
    ts(8, "Throughput de data-service por código",
       [('sum by (status_code)(rate(otelcol_data_service_requests_total{route="/product"}[1m]))', "HTTP {{status_code}}")],
       12, 24, stack=True),
    logs(9, "Logs de error de data-service (Loki · OTLP, con trace_id)",
         '{service_name="data-service"} | severity_text="ERROR" | line_format "trace_id={{.trace_id}} {{__line__}}"',
         0, 32),
], "Módulo B y D: detección de anomalías correlacionada, SLOs y error budget")

for d, f in ((security, "lab4-security.json"), (aiops, "lab4-aiops.json")):
    with open(os.path.join(OUT, f), "w") as fh:
        json.dump(d, fh, indent=1, ensure_ascii=False)
    print("OK →", f)
