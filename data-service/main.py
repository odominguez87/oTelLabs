"""
data-service: FastAPI — Servicio de datos de producto (precio + reputación)

Accede a DOS bases de datos administradas:
  • GCP Cloud SQL (PostgreSQL)  → tabla pricing    (en local: contenedor cloudsql-pg)
  • AWS RDS       (PostgreSQL)  → tabla reviews    (en local: contenedor rds-pg)

Instrumentado con los 3 pilares de OpenTelemetry:
  • Trazas  — spans de BD manuales con OTel DB Semantic Conventions
  • Métricas — db.client.operation.duration (semconv) + SLIs HTTP + seguridad
  • Logs    — OTLP (→ Collector → Loki) + JSON en stdout, ambos con trace_id

Se accede a través de un sidecar Envoy (service mesh L7). Los requests que
llegan sin pasar por el mesh se marcan (via_mesh="false") para detectar
tráfico Este-Oeste no autorizado.
"""

import logging
import os
import random
import threading
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
from psycopg2.pool import ThreadedConnectionPool
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pythonjsonlogger import jsonlogger

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry._logs import set_logger_provider
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.trace import SpanKind, StatusCode

# ── Configuración ─────────────────────────────────────────────────────────────
OTEL_ENDPOINT    = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
EXPORT_INTERVAL  = int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "5000"))
ENV              = os.getenv("ENVIRONMENT", "production")
APP_VERSION      = os.getenv("APP_VERSION", "1.0.0")
API_KEYS         = set(os.getenv("DATA_SERVICE_API_KEYS", "svc-a-key").split(","))
ADMIN_KEY        = os.getenv("DATA_SERVICE_ADMIN_KEY", "chaos-admin-key")

# Cada BD se describe con los atributos que exigen las DB Semantic Conventions
DATABASES = {
    "cloudsql": {
        "dsn": os.getenv("CLOUDSQL_DSN", "postgresql://app:secret@cloudsql-pg:5432/catalog"),
        "server.address": os.getenv("CLOUDSQL_HOST", "cloudsql-pg"),
        "server.port": 5432,
        "db.namespace": "catalog",
        "cloud.provider": "gcp",
        "cloud.platform": "gcp_cloud_sql",
    },
    "rds": {
        "dsn": os.getenv("RDS_DSN", "postgresql://app:secret@rds-pg:5432/reviews"),
        "server.address": os.getenv("RDS_HOST", "rds-pg"),
        "server.port": 5432,
        "db.namespace": "reviews",
        "cloud.provider": "aws",
        "cloud.platform": "aws_rds",
    },
}

# ── Resource ──────────────────────────────────────────────────────────────────
resource = Resource.create({
    SERVICE_NAME: "data-service",
    SERVICE_VERSION: APP_VERSION,
    "deployment.environment": ENV,
    "service.namespace": "observability-lab",
})

# ── Pilar 1: trazas ───────────────────────────────────────────────────────────
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)))
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer("data-service", APP_VERSION)

# ── Pilar 2: métricas ─────────────────────────────────────────────────────────
SECONDS_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 2, 5, 10]
meter_provider = MeterProvider(
    resource=resource,
    metric_readers=[PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=OTEL_ENDPOINT, insecure=True), export_interval_millis=EXPORT_INTERVAL)],
    views=[
        View(instrument_name="db.client.operation.duration",
             aggregation=ExplicitBucketHistogramAggregation(SECONDS_BUCKETS)),
        View(instrument_name="*_duration_seconds",
             aggregation=ExplicitBucketHistogramAggregation(SECONDS_BUCKETS)),
    ],
)
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter("data-service", APP_VERSION)

# Métrica estándar de las DB Semantic Conventions (unidad: segundos)
db_operation_duration = meter.create_histogram(
    "db.client.operation.duration", unit="s", description="Duración de operaciones de base de datos")
# SLIs HTTP del servicio
requests_total = meter.create_counter(
    "data_service_requests_total", description="Requests por ruta, código y si pasaron por el mesh")
request_duration = meter.create_histogram(
    "data_service_request_duration_seconds", unit="s", description="Latencia de data-service")
# Señales de seguridad
auth_failures = meter.create_counter(
    "data_service_auth_failures_total", description="Intentos de autenticación fallidos")

# ── Pilar 3: logs (OTLP + stdout JSON) ────────────────────────────────────────
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTEL_ENDPOINT, insecure=True)))
set_logger_provider(logger_provider)


class OtelJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        ctx = trace.get_current_span().get_span_context()
        if ctx and ctx.is_valid:
            log_record["trace_id"] = format(ctx.trace_id, "032x")
            log_record["span_id"] = format(ctx.span_id, "016x")
        log_record["service"] = "data-service"


stdout_handler = logging.StreamHandler()
stdout_handler.setFormatter(OtelJsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[stdout_handler, LoggingHandler(logger_provider=logger_provider)])
logger = logging.getLogger("data-service")

# ── Pools de conexiones (uno por BD) ──────────────────────────────────────────
# ThreadedConnectionPool de psycopg2 NO espera: si las conexiones en uso llegan a
# maxconn lanza PoolError al instante. FastAPI ejecuta endpoints síncronos en un
# threadpool de hasta 40 hilos, así que con latencia alta (más requests en vuelo)
# el pool se agotaba y devolvía 502 en cascada (hallazgo del experimento de ruido).
# Un semáforo acotado convierte el pool en bloqueante con timeout.
POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
POOL_WAIT_S = float(os.getenv("DB_POOL_WAIT_S", "1.0"))
_pools: dict[str, ThreadedConnectionPool] = {}
_slots: dict[str, threading.BoundedSemaphore] = {}
_pool_lock = threading.Lock()


def pool(db: str) -> ThreadedConnectionPool:
    with _pool_lock:
        if db not in _pools:
            _pools[db] = ThreadedConnectionPool(1, POOL_MAX, DATABASES[db]["dsn"], connect_timeout=2)
            _slots[db] = threading.BoundedSemaphore(POOL_MAX)
        return _pools[db]


@contextmanager
def connection(db: str):
    p = pool(db)
    if not _slots[db].acquire(timeout=POOL_WAIT_S):
        raise psycopg2.pool.PoolError(f"timeout esperando conexión del pool ({db})")
    conn = p.getconn()
    try:
        yield conn
    finally:
        p.putconn(conn)
        _slots[db].release()


@contextmanager
def db_span(db: str, operation: str, collection: str, query: str):
    """Span CLIENT de BD conforme a las OTel DB Semantic Conventions."""
    cfg = DATABASES[db]
    attrs = {
        "db.system.name": "postgresql",
        "db.system": "postgresql",                 # nombre previo, por compatibilidad con backends
        "db.namespace": cfg["db.namespace"],
        "db.collection.name": collection,
        "db.operation.name": operation,
        "db.query.text": query,                    # parametrizada: no contiene valores sensibles
        "server.address": cfg["server.address"],
        "server.port": cfg["server.port"],
        "cloud.provider": cfg["cloud.provider"],
        "cloud.platform": cfg["cloud.platform"],
    }
    metric_attrs = {k: attrs[k] for k in ("db.system.name", "db.namespace", "db.collection.name",
                                           "db.operation.name", "server.address")}
    start = time.perf_counter()
    with tracer.start_as_current_span(f"{operation} {collection}", kind=SpanKind.CLIENT, attributes=attrs) as span:
        try:
            yield span
        except Exception as e:
            err = type(e).__name__
            span.set_attribute("error.type", err)
            if isinstance(e, psycopg2.Error) and e.pgcode:
                span.set_attribute("db.response.status_code", e.pgcode)
            span.record_exception(e)
            span.set_status(StatusCode.ERROR, str(e))
            metric_attrs["error.type"] = err
            raise
        finally:
            db_operation_duration.record(time.perf_counter() - start, metric_attrs)


def query_one(db: str, collection: str, sql: str, params: tuple):
    with connection(db) as conn:
        with db_span(db, "SELECT", collection, sql):
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()


# ── Inyección de fallos (Módulo D) ────────────────────────────────────────────
class Chaos(BaseModel):
    error_rate: float = 0.0       # 0.0–1.0: fracción de requests que fallan
    latency_ms: int = 0           # latencia añadida a TODOS los requests
    error_latency_ms: int = 0     # tiempo que tarda en fallar un request con error (p. ej. timeout de BD)


chaos = Chaos(error_rate=float(os.getenv("CHAOS_ERROR_RATE", "0")),
              latency_ms=int(os.getenv("CHAOS_LATENCY_MS", "0")),
              error_latency_ms=int(os.getenv("CHAOS_ERROR_LATENCY_MS", "0")))

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Data Service", version=APP_VERSION)
FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider, excluded_urls="health")


@app.middleware("http")
async def sli_middleware(request: Request, call_next):
    """SLIs HTTP + marca de tráfico que llega sin pasar por el sidecar Envoy."""
    if request.url.path == "/health":
        return await call_next(request)
    start = time.perf_counter()
    via_mesh = "true" if request.headers.get("x-mesh-sidecar") == "envoy" else "false"
    route = "/admin/chaos" if request.url.path.startswith("/admin") else "/product"
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        requests_total.add(1, {"route": route, "status_code": str(status), "via_mesh": via_mesh})
        request_duration.record(time.perf_counter() - start, {"route": route})
        if via_mesh == "false":
            logger.warning("Request recibido fuera del service mesh",
                           extra={"client_ip": request.client.host if request.client else "?", "path": request.url.path})


def check_api_key(request: Request, key: str | None, valid: set[str]):
    if key in valid:
        return
    reason = "missing" if not key else "invalid"
    auth_failures.add(1, {"reason": reason})
    logger.warning("Autenticación fallida", extra={
        "reason": reason, "client_ip": request.client.host if request.client else "?", "path": request.url.path})
    raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
def health():
    return {"status": "ok", "service": "data-service"}


@app.get("/product/{product_id}")
def get_product(product_id: str, request: Request, x_api_key: str | None = Header(default=None)):
    """Precio (Cloud SQL) + reputación (RDS) de un producto."""
    check_api_key(request, x_api_key, API_KEYS)
    span = trace.get_current_span()
    span.set_attribute("product.id", product_id)

    if chaos.latency_ms:
        time.sleep(chaos.latency_ms / 1000)
    if chaos.error_rate and random.random() < chaos.error_rate:
        if chaos.error_latency_ms:
            time.sleep(chaos.error_latency_ms / 1000)   # el fallo tarda: simula timeout de conexión a la BD
        span.set_attribute("chaos.injected", True)
        span.set_attribute("error.type", "chaos.injected_fault")
        span.set_status(StatusCode.ERROR, "injected fault")
        logger.error("Fallo inyectado (chaos)", extra={"product_id": product_id, "error_rate": chaos.error_rate})
        raise HTTPException(status_code=503, detail="injected fault")

    try:
        price = query_one("cloudsql", "pricing",
                          "SELECT price, currency FROM pricing WHERE product_id = %s", (product_id,))
        rating = query_one("rds", "reviews",
                           "SELECT avg_rating, review_count FROM reviews WHERE product_id = %s", (product_id,))
    except psycopg2.Error as e:
        logger.error("Error de base de datos", extra={"product_id": product_id, "error": type(e).__name__})
        raise HTTPException(status_code=502, detail="database error")

    if not price:
        raise HTTPException(status_code=404, detail="product not found")
    result = {
        "product_id": product_id,
        "price": float(price[0]), "currency": price[1],
        "avg_rating": float(rating[0]) if rating else None,
        "review_count": rating[1] if rating else 0,
    }
    logger.info("Datos de producto servidos", extra={"product_id": product_id, "price": result["price"]})
    return result


@app.post("/admin/chaos")
def set_chaos(cfg: Chaos, request: Request, x_api_key: str | None = Header(default=None)):
    """Activa/desactiva la inyección de fallos (protegido con clave de administrador)."""
    check_api_key(request, x_api_key, {ADMIN_KEY})
    chaos.error_rate = max(0.0, min(cfg.error_rate, 1.0))
    chaos.latency_ms, chaos.error_latency_ms = max(0, cfg.latency_ms), max(0, cfg.error_latency_ms)
    logger.warning("Configuración de chaos actualizada", extra=chaos.model_dump())
    return chaos.model_dump()


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logger.exception("Error no controlado")
    return JSONResponse(status_code=500, content={"detail": "internal error"})
