#!/usr/bin/env python3
"""
collect_evidence.py — Recolecta las 3 señales de observabilidad de una ventana
de experimento y las guarda como JSON para el análisis "esperado vs. obtenido".

  Métricas (SLIs)  → Prometheus  query_range
  Trazas           → Jaeger HTTP API
  Logs             → docker logs de service-a (JSON con trace_id)

Uso: collect_evidence.py <inicio_epoch> <fin_epoch> <directorio_salida>
"""
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone

PROM = "http://localhost:9091"
JAEGER = "http://localhost:16686"

SVC = 'service_name="service-a"'
QUERIES = {
    # SLI-2: latencia (percentiles desde el histograma en segundos)
    "p50_s": f"histogram_quantile(0.50, sum(rate(otelcol_http_request_duration_seconds_bucket{{{SVC}}}[1m])) by (le))",
    "p95_s": f"histogram_quantile(0.95, sum(rate(otelcol_http_request_duration_seconds_bucket{{{SVC}}}[1m])) by (le))",
    "p99_s": f"histogram_quantile(0.99, sum(rate(otelcol_http_request_duration_seconds_bucket{{{SVC}}}[1m])) by (le))",
    # SLI-1 / SLI-3: disponibilidad y error rate
    "error_ratio": f'sum(rate(otelcol_http_requests_total{{{SVC},status="5xx"}}[1m])) / sum(rate(otelcol_http_requests_total{{{SVC}}}[1m]))',
    "availability": f'sum(rate(otelcol_http_requests_total{{{SVC},status="2xx"}}[1m])) / sum(rate(otelcol_http_requests_total{{{SVC}}}[1m]))',
    # SLI-4: throughput y saturación
    "rps": f"sum(rate(otelcol_http_requests_total{{{SVC}}}[1m]))",
    "active_requests": f"sum(otelcol_http_active_requests{{{SVC}}})",
    # Dependencia: llamadas a service-b por resultado
    "service_b_success_rps": 'sum(rate(otelcol_service_b_calls_total{status="success"}[1m]))',
    "service_b_error_rps": 'sum(rate(otelcol_service_b_calls_total{status="error"}[1m]))',
    # Burn rate (ventana corta, SLO 99.5 %)
    "burn_rate_5m": f'(sum(rate(otelcol_http_requests_total{{{SVC},status="5xx"}}[5m])) / sum(rate(otelcol_http_requests_total{{{SVC}}}[5m]))) / 0.005',
}


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def prom_range(query, start, end, step=5):
    qs = urllib.parse.urlencode({"query": query, "start": start, "end": end, "step": step})
    res = get_json(f"{PROM}/api/v1/query_range?{qs}")["data"]["result"]
    if not res:
        return []
    return [[float(t), (None if v in ("NaN", "+Inf", "-Inf") else float(v))] for t, v in res[0]["values"]]


def jaeger_traces(start, end):
    qs = urllib.parse.urlencode({
        "service": "service-a", "operation": "GET /order/{order_id}",
        "start": int(start * 1e6), "end": int(end * 1e6), "limit": 3000,
    })
    traces = get_json(f"{JAEGER}/api/traces?{qs}").get("data", [])
    roots, sb_calls, errors, exc_types = [], [], 0, Counter()
    sample_ok = sample_err = None
    for t in traces:
        spans = t["spans"]
        root = next((s for s in spans if s["operationName"] == "GET /order/{order_id}"), None)
        sb = next((s for s in spans if s["operationName"] == "call.service-b.inventory"), None)
        if root:
            roots.append(root["duration"] / 1000)
        if sb:
            sb_calls.append(sb["duration"] / 1000)
        is_err = False
        for s in spans:
            tags = {tg["key"]: tg["value"] for tg in s.get("tags", [])}
            if tags.get("otel.status_code") == "ERROR" or tags.get("error") is True:
                is_err = True
            for lg in s.get("logs", []):
                f = {x["key"]: x["value"] for x in lg.get("fields", [])}
                if f.get("event") == "exception":
                    exc_types[f.get("exception.type", "?")] += 1
        if is_err:
            errors += 1
            sample_err = sample_err or t["traceID"]
        else:
            sample_ok = sample_ok or t["traceID"]
    return {
        "trazas": len(traces),
        "trazas_con_error": errors,
        "excepciones_por_tipo": dict(exc_types),
        "root_ms": pct(roots),
        "call_service_b_ms": pct(sb_calls),
        "trace_ejemplo_ok": sample_ok,
        "trace_ejemplo_error": sample_err,
    }


def pct(values):
    if not values:
        return {}
    v = sorted(values)
    q = lambda p: v[min(len(v) - 1, int(round(p * (len(v) - 1))))]
    return {"n": len(v), "p50": q(0.50), "p95": q(0.95), "p99": q(0.99), "max": v[-1]}


def logs(start, end):
    iso = lambda e: datetime.fromtimestamp(e, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = subprocess.run(
        ["docker", "logs", "service-a", "--since", iso(start), "--until", iso(end)],
        capture_output=True, text=True,
    )
    by_msg, samples, uvicorn_500 = Counter(), [], 0
    for line in (out.stdout + out.stderr).splitlines():
        if '" 500' in line:
            uvicorn_500 += 1
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        key = f'{obj.get("levelname", "?")} | {obj.get("message", "")}'
        by_msg[key] += 1
        if obj.get("levelname") == "ERROR" and len(samples) < 3:
            samples.append(obj)
    tracebacks = (out.stdout + out.stderr).count("Traceback (most recent call last)")
    exc_lines = Counter(l.strip().split(":")[0] for l in (out.stdout + out.stderr).splitlines()
                        if l.startswith(("httpx.", "httpcore.")))
    return {
        "logs_por_mensaje": dict(by_msg),
        "tracebacks_no_capturados": tracebacks,
        "excepciones_en_tracebacks": dict(exc_lines),
        "access_log_500": uvicorn_500,
        "ejemplos_error": samples,
    }


def main():
    start, end, outdir = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
    series = {name: prom_range(q, start, end) for name, q in QUERIES.items()}
    evidence = {
        "ventana": {"inicio": start, "fin": end},
        "promql": QUERIES,
        "series": series,
        "trazas": jaeger_traces(start, end),
        "logs": logs(start, end),
    }
    with open(f"{outdir}/evidence.json", "w") as f:
        json.dump(evidence, f, indent=2, ensure_ascii=False)
    t = evidence["trazas"]
    print(f"[evidencia] trazas={t['trazas']} con_error={t['trazas_con_error']} "
          f"excepciones={t['excepciones_por_tipo']} call.service-b p95={t['call_service_b_ms'].get('p95')}ms")
    print(f"[evidencia] logs={evidence['logs']['logs_por_mensaje']}")


if __name__ == "__main__":
    main()
