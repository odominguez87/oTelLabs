#!/usr/bin/env python3
"""
experiments.py — Experimentos del Lab 4 con medición automática de resultados.

  noise  (Módulo B) Ruido transitorio NO correlacionado + 1 incidente real.
                    Compara cuántas notificaciones generan las reglas estáticas vs la regla AIOps.
  d1     (Módulo D) +200 ms de latencia de red en service-b (tc netem, rollback gradual).
  d2     (Módulo D) 10 % de errores en data-service (fallos tipo timeout de BD).

Para cada experimento registra la línea de tiempo y calcula:
  MTTD  = primera notificación "firing" recibida por el alert-enricher − inicio de la inyección
  SLO / error budget consumido en la ventana, y si la alerta trae trace_id (accionable).

Uso: python3 lab4/experiments.py {noise|d1|d2}
Requiere el stack levantado y tráfico de fondo (k6) activo.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
PROM = "http://localhost:9091"
DATA_SERVICE = "http://localhost:8002"
ENRICHER = "http://localhost:8080"
ADMIN_KEY = "chaos-admin-key"
SLO = 0.995
BUDGET_WINDOW_DAYS = 28


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def set_chaos(error_rate=0.0, latency_ms=0, error_latency_ms=0):
    body = json.dumps({"error_rate": error_rate, "latency_ms": latency_ms, "error_latency_ms": error_latency_ms}).encode()
    req = urllib.request.Request(f"{DATA_SERVICE}/admin/chaos", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "x-api-key": ADMIN_KEY})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def prom(query, t=None):
    qs = {"query": query}
    if t:
        qs["time"] = t
    with urllib.request.urlopen(f"{PROM}/api/v1/query?{urllib.parse.urlencode(qs)}", timeout=10) as r:
        res = json.load(r)["data"]["result"]
    return float(res[0]["value"][1]) if res else 0.0


def enriched_alerts(since):
    with urllib.request.urlopen(f"{ENRICHER}/alerts", timeout=10) as r:
        rows = json.load(r)
    out = []
    for a in rows:
        ts = datetime.fromisoformat(a["received_at"]).timestamp()
        if ts >= since and a["status"] == "firing":
            a["received_ts"] = ts
            out.append(a)
    return out


def first_alert(alertname, since):
    hits = [a for a in enriched_alerts(since) if a["alertname"] == alertname]
    return min(hits, key=lambda a: a["received_ts"]) if hits else None


def wait_for_alert(alertname, since, timeout_s):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        a = first_alert(alertname, since)
        if a:
            return a
        time.sleep(2)
    return None


def first_breach(query, threshold, start, end, step=5):
    """Primer instante en que `query` supera `threshold` (inicio real de la violación del SLO)."""
    qs = urllib.parse.urlencode({"query": query, "start": start, "end": end, "step": step})
    with urllib.request.urlopen(f"{PROM}/api/v1/query_range?{qs}", timeout=10) as r:
        res = json.load(r)["data"]["result"]
    for t, v in (res[0]["values"] if res else []):
        if float(v) > threshold:
            return float(t)
    return None


def budget(start, end, selector, total_selector):
    """Requests malos en la ventana y % del error budget de 28 días que representan."""
    window = f"{int(end - start)}s"
    bad = prom(f"sum(increase({selector}[{window}]))", end)
    total = prom(f"sum(increase({total_selector}[{window}]))", end)
    rps = total / max(end - start, 1)
    budget_28d = (1 - SLO) * rps * BUDGET_WINDOW_DAYS * 86400
    return {
        "requests": round(total), "malos": round(bad),
        "disponibilidad_ventana": round(1 - bad / total, 5) if total else None,
        "slo_degradado_en_ventana": bool(total and (1 - bad / total) < SLO),
        "budget_28d_requests": round(budget_28d),
        "budget_consumido_pct": round(100 * bad / budget_28d, 3) if budget_28d else None,
    }


def save(name, data):
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, f"{name}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    log(f"Resultados → {path}")


def actionable(a):
    return bool(a and a.get("trace_id") and a.get("runbook"))


# ── Módulo B ──────────────────────────────────────────────────────────────────
NOISE_EVENTS = [
    ("ruido: ráfaga de errores rápidos 3 %", dict(error_rate=0.03)),
    ("ruido: pico de latencia +350 ms", dict(latency_ms=350)),
    ("ruido: ráfaga de errores rápidos 5 %", dict(error_rate=0.05)),
    ("ruido: pico de latencia +400 ms", dict(latency_ms=400)),
]


def exp_noise():
    t0 = time.time()
    timeline = []
    set_chaos()
    log("Control 90 s sin fallos…")
    time.sleep(90)
    for label, cfg in NOISE_EVENTS:
        log(f">>> {label} (40 s)")
        s = time.time()
        set_chaos(**cfg)
        time.sleep(40)
        set_chaos()
        timeline.append({"evento": label, "inicio": s, "fin": time.time(), "tipo": "ruido"})
        log("    pausa 80 s")
        time.sleep(80)
    log(">>> INCIDENTE REAL: 15 % de errores que tardan 900 ms (timeout de BD) durante 120 s")
    s = time.time()
    set_chaos(error_rate=0.15, error_latency_ms=900)
    alert = wait_for_alert("DataServiceAnomalyCorrelated", s, 120)
    time.sleep(max(0, 120 - (time.time() - s)))
    set_chaos()
    timeline.append({"evento": "incidente: errores 15 % + latencia (timeout BD)", "inicio": s, "fin": time.time(),
                     "tipo": "incidente"})
    log("Recuperación 90 s…")
    time.sleep(90)
    t1 = time.time()

    alerts = enriched_alerts(t0)
    by_kind = {}
    for a in alerts:
        if a["kind"] in ("static", "aiops"):
            by_kind.setdefault(a["kind"], []).append(a)

    def classify(a):
        for ev in timeline:
            if ev["inicio"] - 5 <= a["received_ts"] <= ev["fin"] + 75:
                return ev["tipo"]
        return "fuera de evento"

    summary = {}
    for kind in ("static", "aiops"):
        rows = by_kind.get(kind, [])
        summary[kind] = {
            "notificaciones": len(rows),
            "durante_ruido": sum(1 for a in rows if classify(a) == "ruido"),
            "durante_incidente": sum(1 for a in rows if classify(a) == "incidente"),
            "alertas": [{"alertname": a["alertname"], "hora": a["received_at"], "evento": classify(a),
                         "trace_id": a.get("trace_id")} for a in rows],
        }
    st, ai = summary["static"]["notificaciones"], summary["aiops"]["notificaciones"]
    save("exp_noise_vs_aiops", {
        "experimento": "Módulo B — ruido no correlacionado vs incidente real",
        "ventana": {"inicio": t0, "fin": t1}, "timeline": timeline, "resumen": summary,
        "reduccion_ruido_pct": round(100 * (1 - ai / st), 1) if st else None,
        "incidente_detectado_por_aiops": bool(alert),
        "mttd_aiops_s": round(alert["received_ts"] - timeline[-1]["inicio"], 1) if alert else None,
        "alerta_incidente": alert,
    })
    log(f"Estáticas: {st} notificaciones · AIOps: {ai} · incidente detectado: {bool(alert)}")


# ── Módulo D ──────────────────────────────────────────────────────────────────
def exp_d1():
    log("D1: +200 ms de latencia en service-b (tc netem) durante 180 s")
    t0 = time.time()
    proc = subprocess.Popen(
        ["bash", os.path.join(HERE, "..", "chaos", "exp1_latency.sh"), "200", "0", "180", "lab4_d1_latency_200ms"],
        env={**os.environ, "ROLLBACK_MODE": "graceful", "RATE": "10", "PRE_S": "30", "POST_S": "60"})
    # la inyección empieza tras la compuerta de estado estable + PRE_S
    inj_file = os.path.join(HERE, "..", "chaos", "results", "lab4_d1_latency_200ms", "timeline.json")
    proc.wait()
    tl = json.load(open(inj_file))
    analyze_d1(tl["inyeccion"], tl["rollback"], t0)


def analyze_d1(inj, rb, t0=None):
    """MTTD medido desde el INICIO DE LA VIOLACIÓN del SLO (no desde la inyección):
    si la inyección no viola el SLO, lo correcto es no alertar."""
    alert = first_alert("ServiceALatencySLOBreach", inj)
    p95_inj = prom(f"max_over_time(service_a:latency_p95:rate1m[{int(rb - inj) - 15}s])", rb - 15)  # fase estable
    p95_max = prom(f"max_over_time(service_a:latency_p95:rate1m[{int(rb - inj) + 60}s])", rb + 60)
    breach = first_breach("service_a:latency_p95:rate1m", 0.5, inj, rb + 90)
    mttd = round(alert["received_ts"] - breach, 1) if (alert and breach) else None
    save("exp_d1_latency_service_b", {
        "experimento": "Módulo D1 — +200 ms de latencia de red en service-b",
        "inyeccion": inj, "rollback": rb, "inicio": t0,
        "p95_durante_inyeccion_max_s": round(p95_inj, 3),
        "slo_violado_durante_inyeccion": p95_inj > 0.5,
        "inicio_violacion_slo_rel_s": round(breach - inj, 1) if breach else None,
        "violacion_en_transicion_rollback": bool(breach and breach >= rb - 10),
        "alerta": alert, "alerta_rel_inyeccion_s": round(alert["received_ts"] - inj, 1) if alert else None,
        "mttd_s": mttd, "mttd_objetivo_s": 120, "cumple_mttd": bool(mttd is not None and mttd < 120),
        "p95_service_a_max_s": round(p95_max, 3), "slo_latencia_s": 0.5,
        "slo_latencia_degradado": p95_max > 0.5,
        "budget_disponibilidad_service_a": budget(inj, rb, 'otelcol_http_requests_total{service_name="service-a",status="5xx"}',
                                                   'otelcol_http_requests_total{service_name="service-a"}'),
        "alerta_accionable": actionable(alert),
    })


def exp_d2():
    dur = 180
    log(f"D2: 10 % de errores en data-service durante {dur} s")
    set_chaos()
    time.sleep(30)
    inj = time.time()
    set_chaos(error_rate=0.10, error_latency_ms=900)
    alert = wait_for_alert("DataServiceAnomalyCorrelated", inj, dur)
    time.sleep(max(0, dur - (time.time() - inj)))
    set_chaos()
    rb = time.time()
    log("Rollback. Observando recuperación 60 s…")
    time.sleep(60)
    static_first = {n: first_alert(n, inj) for n in ("DataServiceHighErrorRateStatic", "DataServiceHighLatencyStatic")}
    save("exp_d2_errors_data_service", {
        "experimento": "Módulo D2 — 10 % de errores en data-service",
        "inyeccion": inj, "rollback": rb,
        "alerta": alert, "mttd_s": round(alert["received_ts"] - inj, 1) if alert else None,
        "mttd_objetivo_s": 120, "cumple_mttd": bool(alert and alert["received_ts"] - inj < 120),
        "mttd_reglas_estaticas_s": {n: (round(a["received_ts"] - inj, 1) if a else None) for n, a in static_first.items()},
        "error_rate_max": round(prom(f"max_over_time(data_service:error_ratio:rate1m[{int(rb - inj) + 30}s])", rb + 30), 4),
        "burn_rate_max": round(prom(f"max_over_time(data_service:burn_rate:1m[{int(rb - inj) + 30}s])", rb + 30), 1),
        "budget_data_service": budget(inj, rb, 'otelcol_data_service_requests_total{route="/product",status_code=~"5.."}',
                                      'otelcol_data_service_requests_total{route="/product"}'),
        "budget_service_a_usuario_final": budget(inj, rb, 'otelcol_http_requests_total{service_name="service-a",status="5xx"}',
                                                  'otelcol_http_requests_total{service_name="service-a"}'),
        "alerta_accionable": actionable(alert),
    })


if __name__ == "__main__":
    if sys.argv[1] == "d1-analyze":   # recalcula D1 sobre una ejecución ya hecha
        d = json.load(open(os.path.join(RESULTS, "exp_d1_latency_service_b.json")))
        analyze_d1(d["inyeccion"], d["rollback"], d.get("inicio"))
    else:
        {"noise": exp_noise, "d1": exp_d1, "d2": exp_d2}[sys.argv[1]]()
