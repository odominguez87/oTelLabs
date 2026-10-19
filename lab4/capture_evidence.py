#!/usr/bin/env python3
"""
capture_evidence.py — Toma las capturas de evidencia del Lab 4 → lab4/evidence/

  • Paneles de Grafana vía grafana-image-renderer (tema claro, ventana de cada experimento)
  • Jaeger y Prometheus vía Chrome headless (puppeteer-core), con clics para expandir spans

Uso: python3 lab4/capture_evidence.py <ruta a shot.js de puppeteer-core>
"""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
OUT = os.path.join(HERE, "evidence")
GRAFANA = "http://localhost:3000"
SHOT_JS = sys.argv[1]
os.makedirs(OUT, exist_ok=True)


def load(name):
    with open(os.path.join(RES, name)) as f:
        return json.load(f)


def grafana_panel(uid, panel, start, end, out, w=1400, h=480):
    qs = urllib.parse.urlencode({"orgId": 1, "panelId": panel, "width": w, "height": h, "theme": "light",
                                 "tz": "America/Bogota", "from": int(start * 1000), "to": int(end * 1000)})
    with urllib.request.urlopen(f"{GRAFANA}/render/d-solo/{uid}?{qs}", timeout=120) as r:
        open(os.path.join(OUT, out), "wb").write(r.read())
    print("OK →", out)


def grafana_dashboard(uid, start, end, out, w=1600, h=1500):
    qs = urllib.parse.urlencode({"orgId": 1, "width": w, "height": h, "theme": "light", "kiosk": "1",
                                 "tz": "America/Bogota", "from": int(start * 1000), "to": int(end * 1000)})
    with urllib.request.urlopen(f"{GRAFANA}/render/d/{uid}?{qs}", timeout=180) as r:
        open(os.path.join(OUT, out), "wb").write(r.read())
    print("OK →", out)


def browser(specs):
    for s in specs:
        s["out"] = os.path.join(OUT, s["out"])
    subprocess.run(["node", SHOT_JS, json.dumps(specs)], check=True)


noise = load("exp_noise_vs_aiops.json")
d1 = load("exp_d1_latency_service_b.json")
d2 = load("exp_d2_errors_data_service.json")
sec = load("security_demo_result.json")
n0, n1 = noise["ventana"]["inicio"], noise["ventana"]["fin"]

# ── Módulo B: AIOps vs umbrales estáticos ────────────────────────────────────
grafana_panel("lab4-aiops", 1, n0, n1, "B1_error_rate_vs_banda.png")
grafana_panel("lab4-aiops", 2, n0, n1, "B2_p99_vs_slo.png")
grafana_panel("lab4-aiops", 3, n0, n1 + 30, "B3_notificaciones_static_vs_aiops.png")
grafana_panel("lab4-aiops", 4, n0, n1, "B4_alertas_activas.png")

# ── Módulo D: chaos ──────────────────────────────────────────────────────────
grafana_panel("lab4-aiops", 5, d1["inyeccion"] - 90, d1["rollback"] + 90, "D1_p95_service_a_vs_slo.png")
grafana_panel("lab4-aiops", 1, d2["inyeccion"] - 90, d2["rollback"] + 60, "D2_error_rate_vs_banda.png")
grafana_panel("lab4-aiops", 6, d2["inyeccion"] - 90, d2["rollback"] + 60, "D2_burn_rate.png")
grafana_panel("lab4-aiops", 9, d2["inyeccion"], d2["rollback"], "D2_logs_error_trace_id.png", h=420)

# ── Módulo A: métrica semconv de BD ──────────────────────────────────────────
grafana_panel("lab4-aiops", 7, n0 - 600, n0, "A3_db_client_operation_duration.png")

# ── Módulo C: dashboard de seguridad completo ────────────────────────────────
grafana_dashboard("lab4-security", sec["inicio"] - 300, sec["fin"] + 30, "C1_golden_signals_seguridad.png")

# ── Jaeger + Prometheus (navegador) ──────────────────────────────────────────
trace_4 = os.environ.get("TRACE_4SVC", "b2fa448b198dc7ed80ee9f14f0ed704d")
err_trace = (d2.get("alerta") or {}).get("trace_id")
specs = [
    {"url": f"http://localhost:16686/trace/{trace_4}", "out": "A1_jaeger_traza_4_componentes.png",
     "width": 1500, "height": 820},
    {"url": f"http://localhost:16686/trace/{trace_4}", "out": "A2_jaeger_span_db_semconv.png",
     "width": 1500, "height": 1000, "clickText": ["SELECT pricing"], "expandAttrs": True, "scrollText": "SELECT pricing"},
    {"url": "http://localhost:9091/alerts", "out": "B5_prometheus_reglas.png", "width": 1500, "height": 900},
]
if err_trace:
    specs.append({"url": f"http://localhost:16686/trace/{err_trace}", "out": "D2_jaeger_traza_error_alerta.png",
                  "width": 1500, "height": 900})
browser(specs)
