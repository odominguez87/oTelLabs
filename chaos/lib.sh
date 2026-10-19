#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# lib.sh — Esqueleto común de un experimento de caos (Principles of Chaos)
#
#   1. Verificar estado estable (steady state)      → si no, NO se inyecta
#   2. Carga constante con k6 durante todo el experimento
#   3. Fase PRE (control) → INYECCIÓN → fase POST (recuperación)
#   4. Rollback automático en 3 niveles:
#        a) el propio inyector se revierte al cumplir la duración
#        b) criterio de aborto: si k6 aborta (error rate > 5 %) → rollback inmediato
#        c) trap EXIT/INT/TERM: Ctrl+C o fallo del script → rollback
#   5. Recolección de evidencias: métricas, trazas y logs
#
# Cada experimento define: inject(), rollback(), y llama run_experiment.
# ════════════════════════════════════════════════════════════════════════════
set -uo pipefail

export PATH="$HOME/.docker/bin:/opt/homebrew/bin:$PATH"
CHAOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
RATE="${RATE:-20}"
PRE_S="${PRE_S:-45}"     # control antes de inyectar
POST_S="${POST_S:-60}"   # observación de la recuperación

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

# Estado estable = SLO-1 (100 % HTTP 200) Y SLO-2 (p95 < 500 ms) sobre 20 requests.
# Validar solo el status no basta: tras un experimento el sistema puede responder
# 200 pero seguir degradado. Reintenta hasta STEADY_WAIT_S antes de abortar.
steady_state_check() {
  local deadline=$(( $(date +%s) + ${STEADY_WAIT_S:-300} ))
  while :; do
    local ok=0 times=()
    for i in $(seq 1 20); do
      read -r code t < <(curl -s -o /dev/null -w '%{http_code} %{time_total}\n' --max-time 3 \
                          "$BASE_URL/order/ord-00$(( i % 5 + 1 ))")
      [[ "$code" == "200" ]] && ok=$((ok + 1))
      times+=("$t")
    done
    local p95; p95=$(printf '%s\n' "${times[@]}" | sort -n | sed -n '19p')
    if (( ok == 20 )) && awk -v p="$p95" 'BEGIN{exit !(p < 0.5)}'; then
      log "Estado estable OK (200: $ok/20, p95=${p95}s)."
      return
    fi
    if (( $(date +%s) > deadline )); then
      log "ABORTADO antes de iniciar: estado estable no cumplido (200: $ok/20, p95=${p95}s). No se inyecta el fallo."
      exit 1
    fi
    log "Estado estable NO cumplido (200: $ok/20, p95=${p95}s); reintentando en 15s…"
    sleep 15
  done
}

_rolled_back=0
_safe_rollback() {
  (( _rolled_back )) && return
  _rolled_back=1
  log "ROLLBACK: revirtiendo el fallo inyectado…"
  rollback
  ROLLBACK_TS=$(date +%s)
  log "ROLLBACK completado."
}

# run_experiment <nombre> <duración_inyección_s>
run_experiment() {
  local name="$1" dur="$2"
  OUT="$CHAOS_DIR/results/$name"
  mkdir -p "$OUT"
  steady_state_check

  trap '_safe_rollback' EXIT INT TERM

  local total=$(( PRE_S + dur + POST_S ))
  log "k6: ${RATE} rps constantes durante ${total}s (PRE ${PRE_S}s · INYECCIÓN ${dur}s · POST ${POST_S}s)"
  local t0; t0=$(date +%s)
  k6 run --quiet --env BASE_URL="$BASE_URL" --env RATE="$RATE" --env DURATION="${total}s" \
     --env ABORT=true --env SUMMARY="$OUT/k6_summary.json" \
     "$CHAOS_DIR/k6_chaos.js" > "$OUT/k6_stdout.txt" 2>&1 &
  local k6_pid=$!

  sleep "$PRE_S"
  INJECT_TS=$(date +%s)
  log ">>> INYECCIÓN: $DESCRIPTION"
  inject "$dur"

  local aborted=false
  while (( $(date +%s) < INJECT_TS + dur )); do
    if ! kill -0 "$k6_pid" 2>/dev/null; then
      aborted=true
      log "!!! CRITERIO DE ABORTO: k6 se detuvo (error rate > 5 %). Rollback inmediato."
      break
    fi
    sleep 2
  done
  _safe_rollback

  if [[ "$aborted" == false ]]; then
    log "Observando recuperación (${POST_S}s)…"
    wait "$k6_pid"
  else
    wait "$k6_pid" 2>/dev/null
    log "Esperando 30s de recuperación tras el aborto…"
    sleep 30
  fi
  local t1; t1=$(date +%s)
  sleep 20  # margen para que métricas (export 15s + scrape 15s) y trazas lleguen a los backends

  cat > "$OUT/timeline.json" <<EOF
{"experimento": "$name", "descripcion": "$DESCRIPTION", "rate_rps": $RATE,
 "inicio": $t0, "inyeccion": $INJECT_TS, "rollback": $ROLLBACK_TS, "fin": $t1,
 "duracion_planeada_s": $dur, "abortado": $aborted}
EOF
  tail -n 3 "$OUT/k6_stdout.txt"
  log "Recolectando evidencias (Prometheus, Jaeger, logs)…"
  python3 "$CHAOS_DIR/collect_evidence.py" "$t0" "$((t1 + 20))" "$OUT"
  log "Resultados en $OUT"
  trap - EXIT INT TERM
}
