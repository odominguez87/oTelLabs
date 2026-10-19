#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# EXPERIMENTO 3 — Agotamiento de CPU en service-b  (equivalente Litmus pod-cpu-hog)
#
# Hipótesis H3: En estado estable, cuando el CPU disponible para service-b se
#   satura (stress-ng al 100 % en el mismo núcleo), esperamos que el p95 de
#   service-a se mantenga < 500 ms y la disponibilidad ≥ 99.5 %.
#
# Fallo:     service-b y un contenedor stress-ng fijados al MISMO núcleo (cpuset),
#            simulando un "vecino ruidoso" sin afectar al resto del host
# Blast:     1 núcleo de la VM; solo service-b compite por él
# Rollback:  stress-ng --timeout (auto) + kill del contenedor + restaurar cpuset
#
# Uso: ./exp3_cpu_stress.sh [duración_s=120] [núcleo=0]
# ════════════════════════════════════════════════════════════════════════════
source "$(dirname "$0")/lib.sh"

DUR="${1:-120}"; CORE="${2:-0}"
DESCRIPTION="stress-ng --cpu 2 en el núcleo ${CORE} compartido con service-b durante ${DUR}s"
STRESS="alexeiled/stress-ng:latest"
ORIG_CPUSET=$(docker inspect service-b --format '{{.HostConfig.CpusetCpus}}')

inject() {
  docker update --cpuset-cpus "$CORE" service-b >/dev/null
  docker run -d --rm --name chaos-stress --cpuset-cpus "$CORE" "$STRESS" \
    --cpu 2 --cpu-load 100 --timeout "${1}s" --metrics-brief >/dev/null
}

rollback() {
  docker rm -f chaos-stress >/dev/null 2>&1
  local all; all="0-$(( $(docker info --format '{{.NCPU}}') - 1 ))"
  docker update --cpuset-cpus "${ORIG_CPUSET:-$all}" service-b >/dev/null 2>&1 || true
}

docker pull -q "$STRESS" >/dev/null
run_experiment "exp3_cpu_stress" "$DUR"
