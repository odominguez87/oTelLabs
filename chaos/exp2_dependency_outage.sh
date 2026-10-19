#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════
# EXPERIMENTO 2 — Caída de la dependencia service-b  (equivalente Chaos Monkey)
#
# Hipótesis H2: En estado estable, cuando service-b deja de responder,
#   esperamos que service-a degrade con gracia: responde HTTP 200 con
#   inventory.available = -1 y la disponibilidad se mantiene ≥ 99.5 %.
#
# Fallo:     docker pause service-b (congela el proceso: los TCP SYN quedan sin
#            respuesta, como un nodo colgado — peor que un connection refused)
# Blast:     service-b completo; afecta al 100 % de requests de /order
# Rollback:  docker unpause al cumplir la duración / aborto / trap
#
# Uso: ./exp2_dependency_outage.sh [duración_s=60]
# ════════════════════════════════════════════════════════════════════════════
source "$(dirname "$0")/lib.sh"

DUR="${1:-60}"
DESCRIPTION="docker pause service-b durante ${DUR}s"

inject()   { docker pause service-b >/dev/null; }
rollback() { docker unpause service-b >/dev/null 2>&1 || true; }

run_experiment "exp2_dependency_outage" "$DUR"
