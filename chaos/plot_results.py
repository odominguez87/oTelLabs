#!/usr/bin/env python3
"""
plot_results.py — Línea de tiempo de los experimentos a partir de las TRAZAS (Jaeger).

Se usan trazas y no métricas porque cada traza tiene su timestamp exacto; las
métricas llegan con 15–60 s de retraso (export 15 s + scrape 15 s + rate[1m]).

Genera chaos/results/timeline.png y chaos/results/timeline.json
Requiere matplotlib.
"""
import json
import os
import urllib.parse
import urllib.request

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = [
    ("exp0_baseline", "Línea base (sin fallo)", "#8a8f98"),
    ("exp1_latency_300ms", "E1: +300 ms, rollback abrupto", "#d1495b"),
    ("exp1_latency_300ms_graceful", "E1 control: +300 ms, rollback gradual", "#2e86ab"),
]
BUCKET = 10


def traces(tl):
    qs = urllib.parse.urlencode({
        "service": "service-a", "operation": "GET /order/{order_id}",
        "start": int(tl["inicio"] * 1e6), "end": int((tl["fin"] + 30) * 1e6), "limit": 6000,
    })
    with urllib.request.urlopen(f"http://localhost:16686/api/traces?{qs}", timeout=60) as r:
        return json.load(r)["data"]


def series(name):
    tl = json.load(open(f"{HERE}/results/{name}/timeline.json"))
    inj = tl["inyeccion"]
    buckets = {}
    for t in traces(tl):
        root = next((s for s in t["spans"] if s["operationName"] == "GET /order/{order_id}"), None)
        if not root:
            continue
        rel = root["startTime"] / 1e6 - inj
        b = buckets.setdefault(int(rel // BUCKET) * BUCKET, {"lat": [], "err": 0})
        b["lat"].append(root["duration"] / 1000)
        if any(tg["key"] == "error" and tg["value"] is True
               for s in t["spans"] for tg in s.get("tags", [])):
            b["err"] += 1
    out = []
    for k in sorted(buckets):
        v = sorted(buckets[k]["lat"])
        out.append({"t": k, "n": len(v), "p50": v[len(v) // 2],
                    "p95": v[min(len(v) - 1, int(len(v) * 0.95))], "errores": buckets[k]["err"]})
    return tl, out


def main():
    data = {name: series(name) for name, _, _ in RUNS}
    json.dump({k: v[1] for k, v in data.items()}, open(f"{HERE}/results/timeline.json", "w"), indent=1)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 4.6), sharex=True,
                                   gridspec_kw={"height_ratios": [2.2, 1]})
    dur = data["exp1_latency_300ms"][0]["duracion_planeada_s"]
    for ax in (ax1, ax2):
        ax.axvspan(0, dur, color="#f4a259", alpha=0.15, lw=0)
        ax.axvline(dur, color="#555", ls=":", lw=1)
        ax.grid(alpha=0.25, lw=0.6)
        ax.spines[["top", "right"]].set_visible(False)

    for name, label, color in RUNS:
        s = data[name][1]
        x = [p["t"] + BUCKET / 2 for p in s]
        ax1.plot(x, [p["p95"] for p in s], color=color, lw=1.8, label=f"{label} — p95")
        ax1.plot(x, [p["p50"] for p in s], color=color, lw=1, ls="--", alpha=0.8)
        if name != "exp0_baseline":
            ax2.bar([xi + (-2 if "graceful" not in name else 2) for xi in x],
                    [p["errores"] for p in s], width=4, color=color, label=label)

    ax1.axhline(500, color="#333", lw=0.9, ls="-.")
    ax1.text(-44, 560, "SLO p95 < 500 ms", fontsize=7.5, color="#333")
    ax1.text(dur / 2 - 18, 21000, "inyección netem", fontsize=7.5, color="#b5651d")
    ax1.text(dur + 2, 15000, "rollback", fontsize=7.5, color="#555")
    ax1.set_yscale("log")
    ax1.set_ylabel("latencia service-a (ms)\nlínea: p95 · trazo: p50", fontsize=8)
    ax1.legend(fontsize=7, loc="upper left", frameon=False, bbox_to_anchor=(0, 0.93))
    ax2.set_ylabel("trazas con error\n(HTTP 500) / 10 s", fontsize=8)
    ax2.set_xlabel("segundos relativos al inicio de la inyección", fontsize=8)
    ax2.legend(fontsize=7, frameon=False, loc="upper left")
    for ax in (ax1, ax2):
        ax.tick_params(labelsize=7.5)
    fig.tight_layout()
    fig.savefig(f"{HERE}/results/timeline.png", dpi=200)
    print("OK →", f"{HERE}/results/timeline.png")


if __name__ == "__main__":
    main()
