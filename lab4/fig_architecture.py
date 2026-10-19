#!/usr/bin/env python3
"""Diagrama de la arquitectura observable del Lab 4 → lab4/results/fig_arquitectura.png (requiere matplotlib)."""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "fig_arquitectura.png")

fig, ax = plt.subplots(figsize=(11, 6.4))
ax.set_xlim(0, 110)
ax.set_ylim(0, 64)
ax.axis("off")


def box(x, y, w, h, title, sub="", fill="#ffffff", lw=1.2, ls="-", bold=True):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2",
                                fc=fill, ec="#000000", lw=lw, ls=ls))
    ax.text(x + w / 2, y + h / 2 + (1.2 if sub else 0), title, ha="center", va="center",
            fontsize=8.6, fontweight="bold" if bold else "normal", color="#000")
    if sub:
        ax.text(x + w / 2, y + h / 2 - 1.8, sub, ha="center", va="center", fontsize=6.6, color="#222")


def arrow(x1, y1, x2, y2, label="", ls="-", color="#000", lw=1.1, lx=0, ly=0.9):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=9,
                                 lw=lw, ls=ls, color=color, shrinkA=2, shrinkB=2))
    if label:
        ax.text((x1 + x2) / 2 + lx, (y1 + y2) / 2 + ly, label, ha="center", fontsize=6.4, color="#000")


# Zonas
ax.add_patch(FancyBboxPatch((1, 30), 66, 32, boxstyle="round,pad=0.3", fc="#f4f4f4", ec="#777", lw=0.8, ls="--"))
ax.text(2.5, 60, "Plano de datos (servicios)", fontsize=7.5, style="italic", color="#333")
ax.add_patch(FancyBboxPatch((1, 0.6), 108, 26.6, boxstyle="round,pad=0.3", fc="#f4f4f4", ec="#777", lw=0.8, ls="--"))
ax.text(2.5, 25, "Plano de observabilidad (3 pilares + AIOps + seguridad)", fontsize=7.5, style="italic", color="#333")
ax.add_patch(FancyBboxPatch((70, 30), 39, 32, boxstyle="round,pad=0.3", fc="#ffffff", ec="#777", lw=0.8, ls="--"))
ax.text(71.5, 60, "Nube (IaC Terraform validada)", fontsize=7.5, style="italic", color="#333")

# Servicios
box(3, 52, 12, 6, "k6 / clientes", "tráfico Norte-Sur", fill="#e6e6e6")
box(20, 51, 14, 8, "service-a", "FastAPI · orquestador")
box(40, 51, 14, 8, "service-b", "inventario · caché")
box(56, 51, 10, 8, "postgres", "orders")
box(20, 37, 14, 8, "envoy-data", "sidecar mesh L7", fill="#e6e6e6", lw=1.6)
box(40, 37, 14, 8, "data-service", "FastAPI · DB semconv")
box(56, 43, 10, 6, "cloudsql-pg", "catalog", bold=False)
box(56, 33, 10, 6, "rds-pg", "reviews", bold=False)

arrow(15, 55, 20, 55, "HTTP")
arrow(34, 55, 40, 55, "E-W")
arrow(54, 55, 56, 55)
arrow(27, 51, 27, 45, "E-W", lx=3, ly=0)
arrow(34, 41, 40, 41, "x-mesh-sidecar", ly=1.6)
arrow(54, 42, 56, 45.5)
arrow(54, 40, 56, 36.5)

# Nube
box(72, 49, 17, 8, "GCP", "Cloud SQL · Cloud Service Mesh\nVPC Flow Logs · SCC", fill="#ffffff")
box(91, 49, 16, 8, "AWS", "RDS · App Mesh · DevOps Guru\nFlow Logs · Security Hub", fill="#ffffff")
box(72, 33, 35, 11, "Equivalencia local → nube",
    "cloudsql-pg → Cloud SQL   ·   rds-pg → RDS\nEnvoy → Cloud Service Mesh / App Mesh\nreglas μ+2σ → Cloud Monitoring / DevOps Guru",
    fill="#ffffff", bold=True)
arrow(66, 46, 72, 51, ls="--", color="#555")
arrow(66, 36, 91, 49, ls="--", color="#555")

# Observabilidad
box(36, 13, 16, 8, "OTel Collector", "memory_limiter · tail sampling · batch", fill="#e6e6e6", lw=1.6)
box(4, 15, 12, 6, "Jaeger", "trazas (TTL 12 h)")
box(4, 5, 12, 6, "Loki", "logs + trace_id")
box(19, 3, 14, 7, "Prometheus", "SLIs · reglas μ+2σ")
box(57, 15, 14, 6, "Alertmanager", "enrutamiento")
box(74, 15, 16, 6, "alert-enricher", "+ trace_id · runbook", fill="#e6e6e6")
box(93, 15, 14, 6, "Grafana", "AIOps · Seguridad")
box(57, 4, 14, 6, "Trivy", "CVEs de imágenes")
box(74, 4, 16, 6, "cve-exporter", "cve_active")

for (x, y) in ((27, 51), (47, 37), (27, 37), (47, 51)):
    arrow(x, y, 44, 21, ls=":", color="#444", lw=0.8)
ax.text(46, 28, "OTLP (trazas · métricas · logs)", fontsize=6.4, color="#000", ha="left")
arrow(36, 17, 16, 18, "trazas")
arrow(36, 15, 16, 8, "logs")
arrow(40, 13, 30, 10, "métricas", lx=-3)
arrow(33, 7, 57, 16, "alertas", ly=-2.2)
arrow(71, 18, 74, 18)
ax.plot([82, 82, 10, 10], [21.4, 23.6, 23.6, 21.4], ls="--", color="#555", lw=1.1)
ax.text(46, 24.2, "consulta la traza fallida (Jaeger API)", ha="center", fontsize=6.4)
arrow(10, 22.2, 10, 21.2, color="#555")
arrow(71, 7, 74, 7)
ax.plot([82, 82, 26, 26], [4, 1.8, 1.8, 3], ls="--", color="#555", lw=1.1)
ax.text(54, 2.2, "scrape cve_active", ha="center", fontsize=6.4)
arrow(90, 18, 93, 18)

fig.tight_layout()
fig.savefig(OUT, dpi=220)
print("OK →", OUT)
