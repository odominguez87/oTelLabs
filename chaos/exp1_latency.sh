#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# EXPERIMENTO 1 — Latencia de red en service-b  (equivalente Gremlin "Latency")
#
# Hipótesis H1: En estado estable (p95 < 500 ms, disponibilidad ≥ 99.5 %),
#   cuando la red de service-b sufre +DELAY ms de latencia, esperamos que
#   service-a absorba la degradación: p95 aumenta proporcionalmente y la
#   disponibilidad se mantiene ≥ 99.5 % (sin respuestas 5xx).
#
# Fallo:     tc netem delay en eth0 del contenedor service-b (sidecar netshoot
#            que comparte su network namespace; funciona en la VM de Docker Desktop)
# Blast:     solo el tráfico de salida de service-b (1 de 2 servicios de negocio)
# Rollback:  auto al terminar la duración + aborto si error rate > 5 % + trap
#
# Uso: ./exp1_latency.sh [delay_ms=300] [jitter_ms=50] [duración_s=120] [nombre]
#
# ROLLBACK_MODE=abrupt   (default) tc qdisc del: descarta los paquetes en cola
# ROLLBACK_MODE=graceful primero delay 0 ms (vacía la cola sin pérdidas) y luego del
# ════════════════════════════════════════════════════════════════════════════
source "$(dirname "$0")/lib.sh"

DELAY="${1:-300}"; JITTER="${2:-50}"; DUR="${3:-120}"; NAME="${4:-exp1_latency_${DELAY}ms}"
ROLLBACK_MODE="${ROLLBACK_MODE:-abrupt}"
DESCRIPTION="tc netem delay ${DELAY}ms ±${JITTER}ms en service-b durante ${DUR}s (rollback ${ROLLBACK_MODE})"
NETSHOOT="nicolaka/netshoot:v0.13"

if [[ "$ROLLBACK_MODE" == "graceful" ]]; then
  REVERT="tc qdisc change dev eth0 root netem delay 0ms; sleep 3; tc qdisc del dev eth0 root"
else
  REVERT="tc qdisc del dev eth0 root"
fi

inject() {
  local netem="delay ${DELAY}ms"
  (( JITTER > 0 )) && netem="$netem ${JITTER}ms distribution normal"
  # Nivel (a) de rollback: el sidecar elimina la qdisc solo al cumplir la duración
  docker run -d --rm --name chaos-netem --net container:service-b --cap-add NET_ADMIN "$NETSHOOT" \
    sh -c "tc qdisc add dev eth0 root netem $netem && tc qdisc show dev eth0 && sleep $1; $REVERT" >/dev/null
  sleep 1
  docker logs chaos-netem 2>&1 | sed 's/^/    tc: /'
}

rollback() {
  # Si el sidecar sigue vivo (aborto / Ctrl+C) se revierte desde un contenedor nuevo
  if docker ps -q -f name=chaos-netem | grep -q .; then
    docker run --rm --net container:service-b --cap-add NET_ADMIN "$NETSHOOT" \
      sh -c "$REVERT" >/dev/null 2>&1
    docker rm -f chaos-netem >/dev/null 2>&1
  fi
  docker run --rm --net container:service-b --cap-add NET_ADMIN "$NETSHOOT" \
    tc qdisc del dev eth0 root >/dev/null 2>&1 || true
  docker run --rm --net container:service-b --cap-add NET_ADMIN "$NETSHOOT" \
    tc qdisc show dev eth0 | sed 's/^/    tc (post-rollback): /'
}

docker pull -q "$NETSHOOT" >/dev/null
run_experiment "$NAME" "$DUR"
