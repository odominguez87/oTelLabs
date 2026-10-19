# Laboratorio 3 — Game Day de Chaos Engineering

Scripts, resultados y capturas (`screenshots/`) de los experimentos de caos sobre el stack OTel.

## Requisitos
- Stack levantado: `docker compose up -d` (desde la raíz del repo)
- `k6` (`brew install k6`)
- `tc netem` y `stress-ng` corren en contenedores auxiliares (`nicolaka/netshoot`, `alexeiled/stress-ng`), así que no hace falta instalarlos en el host. Funcionan en Docker Desktop para macOS.

## Experimentos

| Script | Hipótesis | Fallo |
|---|---|---|
| `baseline.sh [s]` | — | Control: misma carga, sin fallo |
| `exp1_latency.sh [delay_ms] [jitter_ms] [s] [nombre]` | H1 | `tc netem delay` en service-b (`ROLLBACK_MODE=abrupt\|graceful`) |
| `exp2_dependency_outage.sh [s]` | H2 | `docker pause service-b` |
| `exp3_cpu_stress.sh [s] [núcleo]` | H3 | `stress-ng` en el mismo núcleo que service-b |

Cada experimento ([lib.sh](lib.sh)):
1. Verifica el estado estable: 20/20 respuestas HTTP 200 y p95 < 500 ms. Si no se cumple, no inyecta.
2. Genera carga constante con k6 a 20 rps ([k6_chaos.js](k6_chaos.js)) en tres fases: control de 45 s, inyección y recuperación de 60 s.
3. Hace rollback automático en tres niveles: autoexpiración del inyector, aborto si el error rate supera el 5 % y `trap` ante Ctrl+C.
4. Recolecta evidencias en `results/<experimento>/` ([collect_evidence.py](collect_evidence.py)): series de Prometheus, trazas de Jaeger y logs.

## Regenerar la figura de línea de tiempo
```bash
python3 -m venv .venv && .venv/bin/pip install matplotlib
.venv/bin/python chaos/plot_results.py     # results/timeline.png (lee las trazas de Jaeger)
```
