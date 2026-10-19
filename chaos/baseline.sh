#!/usr/bin/env bash
# Línea base (estado estable) sin inyección: mismo perfil de carga que los experimentos.
# Uso: ./baseline.sh [duración_s=120]
source "$(dirname "$0")/lib.sh"

DUR="${1:-120}"
DESCRIPTION="sin fallo (control / estado estable)"
inject()   { :; }
rollback() { :; }

run_experiment "exp0_baseline" "$DUR"
