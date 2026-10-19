#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# Módulo C — Demostración de las señales y alertas de seguridad
#
#   1. Fuerza bruta: 4 req/s con API keys inválidas a través del mesh (90 s)
#      → data_service_auth_failures_total ↑ → alerta AuthFailuresSpike
#   2. Bypass del mesh: requests directos a data-service:8002 sin pasar por Envoy (60 s)
#      → via_mesh="false" → alerta UnmeshedEastWestTraffic
#
# Las alertas llegan enriquecidas al alert-enricher: curl localhost:8080/alerts
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail
OUT="$(dirname "$0")/results/security_demo_result.json"
start=$(date +%s)

echo "[$(date +%T)] 1) Fuerza bruta con claves inválidas vía mesh (90 s)…"
end=$((SECONDS + 90))
i=0
while (( SECONDS < end )); do
  curl -s -o /dev/null -H "x-api-key: guess-$((RANDOM))" "http://localhost:10000/product/LAPTOP-X1" &
  (( i % 3 == 0 )) && curl -s -o /dev/null "http://localhost:10000/product/MOUSE-PRO" &   # sin clave
  i=$((i + 1)); sleep 0.25
done
wait
bf_end=$(date +%s)

echo "[$(date +%T)] 2) Tráfico directo a data-service:8002 saltándose el sidecar (60 s)…"
end=$((SECONDS + 60))
while (( SECONDS < end )); do
  curl -s -o /dev/null -H "x-api-key: svc-a-key" "http://localhost:8002/product/HEADSET-Z"
  sleep 0.5
done
bypass_end=$(date +%s)

echo "[$(date +%T)] Esperando 45 s para que las alertas se evalúen y notifiquen…"
sleep 45
python3 - "$start" "$bf_end" "$bypass_end" "$OUT" <<'PY'
import json, sys, time, urllib.request
from datetime import datetime
start, bf_end, bypass_end, out = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
rows = json.load(urllib.request.urlopen("http://localhost:8080/alerts"))
alertas = []
for a in rows:
    ts = datetime.fromisoformat(a["received_at"]).timestamp()
    if ts >= start and a["kind"] == "security" and a["status"] == "firing":
        alertas.append({"alertname": a["alertname"], "segundos": round(ts - start), "trace_id": a.get("trace_id"),
                        "summary": a["summary"]})
        print(f"  {a['alertname']:26} +{ts - start:5.0f}s  trace_id={a.get('trace_id')}  {a['summary']}")
json.dump({"inicio": start, "fin_fuerza_bruta": bf_end, "fin_bypass": bypass_end, "fin": int(time.time()),
           "alertas": alertas}, open(out, "w"), indent=2, ensure_ascii=False)
PY
