"""
service-a: FastAPI — Orquestador principal
Recibe requests HTTP externos, hace llamadas a service-b y accede a PostgreSQL.
Instrumentado con OpenTelemetry (OTel) SDK: trazas, métricas y logs correlacionados.
"""

import logging
import os
import time
import json
import psycopg2
import httpx
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from pythonjsonlogger import jsonlogger

# ── OTel SDK: imports ────────────────────────────────────────────────────────
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
from opentelemetry.propagate import inject

# ── Configuración desde variables de entorno ─────────────────────────────────
OTEL_ENDPOINT   = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
SERVICE_B_URL   = os.getenv("SERVICE_B_URL", "http://service-b:8001")
# data-service se consume a través de su sidecar Envoy (service mesh L7)
DATA_SERVICE_URL = os.getenv("DATA_SERVICE_URL", "http://envoy-data:10000")
DATA_SERVICE_KEY = os.getenv("DATA_SERVICE_API_KEY", "svc-a-key")
DB_DSN          = os.getenv("DATABASE_URL", "postgresql://app:secret@postgres:5432/appdb")
PROMETHEUS_PORT = int(os.getenv("PROMETHEUS_PORT", "9090"))
ENV             = os.getenv("ENVIRONMENT", "production")
APP_VERSION     = os.getenv("APP_VERSION", "1.0.0")

# ── 1. Resource: identidad del servicio en toda la telemetría ─────────────────
# El Resource viaja en cada señal (traza, métrica, log) para identificar el origen.
resource = Resource.create({
    SERVICE_NAME:    "service-a",          # Nombre del servicio en Jaeger/Grafana
    SERVICE_VERSION: APP_VERSION,          # Para correlacionar incidentes con releases
    "deployment.environment": ENV,         # production / staging / development
    "cloud.provider": os.getenv("CLOUD_PROVIDER", "gcp"),
    "host.name":     os.getenv("HOSTNAME", "local"),
})

# ── 2. TracerProvider + OTLP exporter (gRPC → OTel Collector) ────────────────
tracer_provider = TracerProvider(resource=resource)
otlp_span_exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)

# BatchSpanProcessor: agrupa spans en memoria antes de enviarlos al Collector
# → Reduce el overhead de red: en lugar de 1 request HTTP por span,
#   envía lotes de hasta 512 spans cada 5 segundos
tracer_provider.add_span_processor(BatchSpanProcessor(otlp_span_exporter))
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer("service-a", APP_VERSION)

# ── 3. MeterProvider + Prometheus reader (scraping en :9090/metrics) ──────────
# PrometheusMetricReader expone las métricas OTel en formato Prometheus
prometheus_reader = PrometheusMetricReader()

# Reader 2: envía métricas al Collector cada 15 segundos (OTLP push)
otlp_metric_reader = PeriodicExportingMetricReader(
    OTLPMetricExporter(endpoint=OTEL_ENDPOINT, insecure=True),
    export_interval_millis=int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "15000")),
)

# Buckets en SEGUNDOS: los defaults del SDK (5, 10, 25 … 10000) asumen ms y
# dejarían todos los requests en le=5 → histogram_quantile inservible.
latency_view = View(
    instrument_name="*_duration_seconds",
    aggregation=ExplicitBucketHistogramAggregation(
        [0.025, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1, 2, 3, 5, 7.5, 10]
    ),
)

meter_provider = MeterProvider(
    resource=resource,
    metric_readers=[prometheus_reader, otlp_metric_reader],  # Los dos simultáneamente
    views=[latency_view],
)
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter("service-a", APP_VERSION)

# ── 4. Instrumentos de métricas (SLIs) ────────────────────────────────────────

# SLI-1: Disponibilidad
http_requests_total = meter.create_counter(
    "http_requests_total",
    description="Total HTTP requests recibidos",
)
# → Prometheus query: rate(http_requests_total{status=~"2.."}[5m]) / rate(http_requests_total[5m])

# SLI-2: Latencia (histograma para percentiles p50/p95/p99)
http_request_duration = meter.create_histogram(
    "http_request_duration_seconds",
    description="Distribución de latencia HTTP",
)
# → Prometheus query: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

# SLI-3: Error Rate (derivado de http_requests_total con status=5xx)

# SLI-4: Saturación
active_requests = meter.create_up_down_counter(
    "http_active_requests",
    description="Requests activos en vuelo",
)

# Métricas de dependencias
db_query_duration = meter.create_histogram(
    "db_query_duration_seconds",
    description="Latencia de consultas a PostgreSQL",
    unit="s",
)
service_b_calls_total = meter.create_counter(
    "service_b_calls_total",
    description="Llamadas a service-b por resultado",
)
data_service_calls_total = meter.create_counter(
    "data_service_calls_total",
    description="Llamadas a data-service (vía mesh) por resultado",
)

# ── 5. Logging estructurado JSON con trace_id/span_id ────────────────────────
# El trace_id en el log es el PUENTE que correlaciona log ↔ traza en Grafana.
class OtelJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        span = trace.get_current_span()
        ctx  = span.get_span_context()
        if ctx and ctx.is_valid:
            # trace_id en formato hexadecimal de 32 dígitos (estándar W3C)
            log_record["trace_id"] = format(ctx.trace_id, "032x")
            log_record["span_id"]  = format(ctx.span_id, "016x")
        log_record["service"] = "service-a"

handler = logging.StreamHandler()
handler.setFormatter(OtelJsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("service-a")

# ── 6. Auto-instrumentación de librerías ─────────────────────────────────────
# FastAPI se instrumenta con instrument_app(app) después de crear la app (ver abajo)

HTTPXClientInstrumentor().instrument(tracer_provider=tracer_provider)
# → Inyecta automáticamente el header W3C traceparent en cada llamada a service-b
# → El header propagado: "traceparent: 00-{trace_id}-{span_id}-01"

Psycopg2Instrumentor().instrument(tracer_provider=tracer_provider)
# → Crea spans para cada query SQL con: db.statement, db.operation, db.name

# ── Conexión DB ───────────────────────────────────────────────────────────────
def get_db_connection():
    return psycopg2.connect(DB_DSN)

# ── FastAPI App ───────────────────────────────────────────────────────────────
# Clientes HTTP compartidos con keep-alive (remediación P2 del Lab 3): crear un
# httpx.AsyncClient cuesta ~28 ms de CPU; hacerlo por request saturaba service-a.
http_clients: dict[str, httpx.AsyncClient] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No se necesita iniciar porque ya PrometheusmETRIC EN LINA 56 ya lo hizo en el serv
    # start_http_server(PROMETHEUS_PORT)
    logger.info("Prometheus metrics server started", extra={"port": PROMETHEUS_PORT})
    http_clients["service-b"] = httpx.AsyncClient(timeout=5.0)
    http_clients["data-service"] = httpx.AsyncClient(timeout=httpx.Timeout(1.5, connect=0.3))
    yield
    for client in http_clients.values():
        await client.aclose()
    # Shutdown: flushar todos los spans pendientes
    tracer_provider.shutdown()
    meter_provider.shutdown()

app = FastAPI(
    title="Service A",
    description="Microservicio orquestador — OTel end-to-end lab",
    version=APP_VERSION,
    lifespan=lifespan,
)

# instrument_app sobre la instancia: instrument() parchea fastapi.FastAPI, pero
# este módulo ya importó la clase original, así que no tendría efecto.
FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider)
# → Crea automáticamente un span por cada endpoint HTTP
# → Atributos: http.method, http.route, http.status_code, http.url

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check — no genera trazas (excluido en el Collector)."""
    return {"status": "ok", "service": "service-a"}


@app.get("/order/{order_id}")
async def get_order(order_id: str, request: Request):
    """
    Flujo principal:
    1. Consulta la DB para obtener metadatos del pedido (custom span: fetch.order.db)
    2. Llama a service-b para enriquecer con datos de inventario (propagación W3C)
    3. Retorna la respuesta consolidada
    """
    start = time.time()
    labels = {"endpoint": "/order", "method": "GET"}
    active_requests.add(1, labels)
    status_labels = {**labels, "status": "5xx"}  # se sobrescribe si el request termina bien

    try:
        # ── Custom span: lógica de negocio DB ────────────────────────────────
        with tracer.start_as_current_span(
            "fetch.order.db",                      # Nombre del span en Jaeger
            kind=trace.SpanKind.CLIENT,
            attributes={
                "db.system":    "postgresql",
                "db.operation": "SELECT",
                "order.id":     order_id,          # Atributo de negocio custom
            }
        ) as db_span:
            # El código dentro del `with` está cubierto por el span
            # Si lanza una excepción, el span se marca como ERROR automáticamente
            db_start = time.time()
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, product, quantity, status FROM orders WHERE id = %s",
                    (order_id,)
                )
                row = cur.fetchone()
                conn.close()

                db_duration = time.time() - db_start
                db_query_duration.record(db_duration, {"operation": "SELECT", "table": "orders"})

                if not row:
                    status_labels = {**labels, "status": "4xx"}
                    db_span.set_status(trace.StatusCode.ERROR, "Order not found")
                    raise HTTPException(status_code=404, detail=f"Order {order_id} not found")

                order_data = {
                    "id":       row[0],
                    "product":  row[1],
                    "quantity": row[2],
                    "status":   row[3],
                }
                db_span.set_attribute("order.status", order_data["status"])
                logger.info("Order fetched from DB", extra={"order_id": order_id, "status": order_data["status"]})

            except HTTPException:
                raise
            except Exception as e:
                db_span.record_exception(e)
                db_span.set_status(trace.StatusCode.ERROR, str(e))
                logger.error("DB query failed", extra={"error": str(e), "order_id": order_id})
                raise HTTPException(status_code=500, detail="Database error")

        # ── Llamada a service-b (propagación automática de trace context) ────
        with tracer.start_as_current_span(
            "call.service-b.inventory",
            kind=trace.SpanKind.CLIENT,
            attributes={
                "http.method": "GET",
                "peer.service": "service-b",
                "order.product": order_data["product"],
            }
        ) as sb_span:
            service_b_calls_total.add(1, {"status": "attempt"})
            try:
                # HTTPXClientInstrumentor inyecta automáticamente el header
                # traceparent: 00-{trace_id}-{span_id}-01 (W3C TraceContext)
                headers = {}
                inject(headers)  # propagación explícita como fallback
                resp = await http_clients["service-b"].get(
                    f"{SERVICE_B_URL}/inventory/{order_data['product']}",
                    headers=headers,
                )
                resp.raise_for_status()
                inventory = resp.json()

                service_b_calls_total.add(1, {"status": "success"})
                sb_span.set_attribute("http.status_code", resp.status_code)
                sb_span.set_attribute("inventory.available", inventory.get("available", 0))
                logger.info("Inventory fetched from service-b",
                            extra={"product": order_data["product"], "available": inventory.get("available")})
                status_labels = {**labels, "status": "2xx"} 
            except httpx.HTTPStatusError as e:
                service_b_calls_total.add(1, {"status": "error"})
                sb_span.record_exception(e)
                sb_span.set_status(trace.StatusCode.ERROR, str(e))
                status_labels = {**labels, "status": f"{e.response.status_code // 100}xx"}
                logger.error("service-b call failed", extra={"error": str(e)})
                inventory = {"available": -1, "error": "service-b unavailable"}

        # ── Llamada a data-service vía mesh (Envoy) ──────────────────────────
        # Captura TODO error de transporte y degrada con gracia (remediación P1 del Lab 3)
        with tracer.start_as_current_span(
            "call.data-service.product",
            kind=trace.SpanKind.CLIENT,
            attributes={"peer.service": "data-service", "order.product": order_data["product"]},
        ) as ds_span:
            try:
                resp = await http_clients["data-service"].get(
                    f"{DATA_SERVICE_URL}/product/{order_data['product']}",
                    headers={"x-api-key": DATA_SERVICE_KEY})
                resp.raise_for_status()
                product_data = resp.json()
                data_service_calls_total.add(1, {"status": "success"})
            except (httpx.HTTPError, ValueError) as e:
                data_service_calls_total.add(1, {"status": "error"})
                ds_span.record_exception(e)
                ds_span.set_status(trace.StatusCode.ERROR, str(e))
                logger.error("data-service call failed", extra={"error": type(e).__name__})
                product_data = {"price": None, "degraded": True}

        return {
            "order":     order_data,
            "inventory": inventory,
            "product":   product_data,
            "trace_id":  format(trace.get_current_span().get_span_context().trace_id, "032x"),
        }

    finally:
        # Registrar SLIs una sola vez por request (incluye 4xx/5xx y excepciones)
        http_requests_total.add(1, status_labels)
        http_request_duration.record(time.time() - start, labels)
        active_requests.add(-1, labels)


@app.get("/metrics/health")
async def metrics_health():
    """Estado del pipeline de telemetría."""
    return {
        "otel_collector": OTEL_ENDPOINT,
        "prometheus_port": PROMETHEUS_PORT,
        "service_b_url": SERVICE_B_URL,
    }
