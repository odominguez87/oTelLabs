/**
 * k6_chaos.js — Carga constante para los experimentos de Game Day
 *
 * Tasa de llegada CONSTANTE (no VUs): si el sistema se degrada, k6 sigue
 * enviando la misma carga, igual que usuarios reales que no "esperan".
 *
 * Variables:
 *   BASE_URL  (default http://127.0.0.1:8000)
 *   RATE      requests/segundo (default 20)
 *   DURATION  duración (default 3m)
 *   ABORT     "true" → aborta la prueba si error rate > 5 % (criterio de aborto del experimento)
 *   SUMMARY   ruta del JSON de resumen (default summary.json)
 */
import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";
const RATE     = parseInt(__ENV.RATE || "20");
const DURATION = __ENV.DURATION || "3m";
const ABORT    = (__ENV.ABORT || "false") === "true";

// Solo pedidos válidos: en estado estable el error rate esperado es 0 %
const ORDERS = ["ord-001", "ord-002", "ord-003", "ord-004", "ord-005"];

export const options = {
  scenarios: {
    steady: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: 50,
      maxVUs: 400,
    },
  },
  thresholds: {
    // SLO-1 disponibilidad ≥ 99.5 %  → error rate ≤ 0.5 %
    // Criterio de ABORTO (blast radius): error rate > 5 % sostenido 20 s
    http_req_failed: ABORT
      ? [{ threshold: "rate<0.05", abortOnFail: true, delayAbortEval: "20s" }]
      : ["rate<0.005"],
    // SLO-2 latencia p95 < 500 ms
    http_req_duration: ["p(95)<500"],
  },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export default function () {
  const id = ORDERS[Math.floor(Math.random() * ORDERS.length)];
  const res = http.get(`${BASE_URL}/order/${id}`, { timeout: "15s", tags: { name: "GET /order/{id}" } });
  check(res, {
    "status 200": (r) => r.status === 200,
    "inventario disponible (no degradado)": (r) => {
      try { return r.json("inventory.available") >= 0; } catch (_) { return false; }
    },
  });
}

export function handleSummary(data) {
  const m = data.metrics;
  const d = m.http_req_duration.values;
  const out = {
    rate_objetivo_rps: RATE,
    duracion_configurada: DURATION,
    // Con ABORT=true, un threshold fallido de http_req_failed implica que k6 abortó
    abortado: ABORT && Object.values(m.http_req_failed.thresholds || {}).some((t) => !t.ok),
    duracion_real_s: data.state ? data.state.testRunDurationMs / 1000 : null,
    requests: m.http_reqs.values.count,
    rps: m.http_reqs.values.rate,
    error_rate_pct: 100 * m.http_req_failed.values.rate,
    latencia_ms: {
      avg: d.avg, p50: d.med, p90: d["p(90)"], p95: d["p(95)"], p99: d["p(99)"], max: d.max,
    },
    checks: (data.root_group.checks || []).map((c) => ({ name: c.name, passes: c.passes, fails: c.fails })),
    dropped_iterations: m.dropped_iterations ? m.dropped_iterations.values.count : 0,
  };
  const path = __ENV.SUMMARY || "summary.json";
  const line = `\n[resumen] reqs=${out.requests} rps=${out.rps.toFixed(1)} ` +
    `err=${out.error_rate_pct.toFixed(2)}% p50=${d.med.toFixed(0)}ms p95=${d["p(95)"].toFixed(0)}ms ` +
    `p99=${d["p(99)"].toFixed(0)}ms max=${d.max.toFixed(0)}ms\n`;
  return { [path]: JSON.stringify(out, null, 2), stdout: line };
}
