#!/usr/bin/env python3
"""
alert-enricher — Webhook de Alertmanager que convierte una alerta en una alerta ACCIONABLE.

Para cada alerta "firing":
  1. Busca en Jaeger la traza con error más reciente del servicio afectado
  2. Adjunta trace_id, enlace a la traza, enlace a los logs (Loki por trace_id) y runbook
  3. Persiste la alerta enriquecida (JSONL) y la expone en /alerts y como métricas en /metrics

Equivalente en nube: Cloud Monitoring notification channel → Cloud Function /
EventBridge rule (DevOps Guru insight) → Lambda.
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

JAEGER = os.getenv("JAEGER_URL", "http://jaeger:16686")
JAEGER_PUBLIC = os.getenv("JAEGER_PUBLIC_URL", "http://localhost:16686")
GRAFANA_PUBLIC = os.getenv("GRAFANA_PUBLIC_URL", "http://localhost:3000")
STORE = os.getenv("ALERT_STORE", "/data/alerts.jsonl")

_lock = threading.Lock()
_counts: Counter = Counter()


def latest_error_trace(service: str, lookback_s: int = 300):
    """Traza con error más reciente del servicio (o de la ruta crítica si no hay)."""
    end = int(time.time() * 1e6)
    for svc in dict.fromkeys([service, "service-a"]):
        qs = urllib.parse.urlencode({"service": svc, "tags": json.dumps({"error": "true"}),
                                     "start": end - lookback_s * 1_000_000, "end": end, "limit": 20})
        try:
            with urllib.request.urlopen(f"{JAEGER}/api/traces?{qs}", timeout=5) as r:
                traces = json.load(r).get("data", [])
        except Exception as e:  # Jaeger caído: la alerta sale igual, sin trace_id
            return {"trace_lookup_error": type(e).__name__}
        if traces:
            t = max(traces, key=lambda t: max(s["startTime"] for s in t["spans"]))
            err = next((s for s in t["spans"]
                        if any(tg["key"] == "error" and tg["value"] is True for tg in s.get("tags", []))), t["spans"][0])
            tags = {tg["key"]: tg["value"] for tg in err.get("tags", [])}
            return {
                "trace_id": t["traceID"],
                "trace_service": svc,
                "failed_span": err["operationName"],
                "error_type": tags.get("error.type") or tags.get("otel.status_description"),
                "trace_url": f"{JAEGER_PUBLIC}/trace/{t['traceID']}",
                "logs_url": f"{GRAFANA_PUBLIC}/explore?left=" + urllib.parse.quote(json.dumps(
                    {"datasource": "loki", "queries": [{"refId": "A", "expr": f'{{service_name=~".+"}} | trace_id="{t["traceID"]}"'}]})),
            }
    return {"trace_id": None}


def enrich(kind: str, alert: dict) -> dict:
    labels, ann = alert.get("labels", {}), alert.get("annotations", {})
    rec = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "status": alert.get("status"),
        "alertname": labels.get("alertname"),
        "severity": labels.get("severity"),
        "service": labels.get("service"),
        "starts_at": alert.get("startsAt"),
        "summary": ann.get("summary"),
        "context": {k: v for k, v in ann.items() if k not in ("summary", "runbook")},
        "runbook": ann.get("runbook"),
    }
    if alert.get("status") == "firing":
        rec.update(latest_error_trace(labels.get("service", "service-a")))
    return rec


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        kind = self.path.rsplit("/", 1)[-1]
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        out = []
        for a in payload.get("alerts", []):
            rec = enrich(kind, a)
            out.append(rec)
            with _lock:
                if rec["status"] == "firing":
                    _counts[(rec["alertname"], kind)] += 1
                with open(STORE, "a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(json.dumps({"msg": "alerta enriquecida", **rec}, ensure_ascii=False), flush=True)
        self._send(200, json.dumps({"enriched": len(out)}))

    def do_GET(self):
        if self.path.startswith("/alerts"):
            try:
                rows = [json.loads(l) for l in open(STORE)]
            except FileNotFoundError:
                rows = []
            return self._send(200, json.dumps(rows, ensure_ascii=False, indent=1))
        if self.path.startswith("/metrics"):
            lines = ["# HELP enriched_alerts_total Notificaciones de alerta (firing) recibidas y enriquecidas",
                     "# TYPE enriched_alerts_total counter"]
            with _lock:
                for (name, kind), n in sorted(_counts.items()):
                    lines.append(f'enriched_alerts_total{{alertname="{name}",kind="{kind}"}} {n}')
            return self._send(200, "\n".join(lines) + "\n", "text/plain; version=0.0.4")
        self._send(200, json.dumps({"status": "ok"}))


if __name__ == "__main__":
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    print("alert-enricher escuchando en :8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
