"""LGTM observability P0 — foundations.

Single source for the three foundational primitives that all engines plug
into :

1. **Cycle ID propagation** — `cycle_id_var` ContextVar + `new_cycle()` helper.
   Generates a UUID hex per cycle and binds it to ``structlog.contextvars``
   so every log line during that cycle carries ``cycle_id`` automatically.
   `trace_id_var` is reserved for Phase 2 (OTel) where it will hold the
   32-char hex trace_id of the parent span ; for P0 it stays None.

2. **Prometheus metrics** — Counter / Histogram / Gauge for cycle throughput,
   duration, freshness + IB session state. Naming follows
   ``<namespace>_<subsystem>_<name>_<unit>`` per
   `docs/observability/CONVENTIONS.md`.

3. **`observed_cycle(engine)` contextmanager** — one-call wrapper that
   handles new_cycle(), starts a perf timer, increments cycles_total with
   status ok/error, observes cycle_duration, sets last_cycle_ts on exit.
   Catches exceptions to flip status='error' then re-raises.

Spec : ``docs/LGTM_IMPLEMENTATION_SPEC.md`` § Phase 0.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator
from uuid import uuid4

import structlog
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)

# ── ContextVars ──────────────────────────────────────────────────────────────
# These are exposed as Python ContextVars for Phase 2 OTel propagation (where
# the OTel SDK needs to read the current cycle_id to attach as a span
# attribute). For P0, only `cycle_id_var` is set ; structlog auto-injects it
# in log lines via structlog.contextvars.bind_contextvars.

cycle_id_var: ContextVar[str | None] = ContextVar("cycle_id", default=None)
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_cycle() -> str:
    """Generate a fresh cycle_id, set it on both ContextVar + structlog.

    Returns the hex string so callers can echo it in initial log lines if
    desired. Subsequent log calls in the same async task / thread will
    inherit `cycle_id` automatically via structlog.contextvars processor.
    """
    cid = uuid4().hex
    cycle_id_var.set(cid)
    structlog.contextvars.bind_contextvars(cycle_id=cid)
    return cid


def clear_cycle() -> None:
    """Reset cycle_id at the end of a cycle. Optional — useful in tests."""
    cycle_id_var.set(None)
    structlog.contextvars.unbind_contextvars("cycle_id")


# ── Prometheus metrics ───────────────────────────────────────────────────────
# Single shared registry (the default global one). Each engine process has
# its own /metrics endpoint, so cross-engine cardinality is naturally
# partitioned. Labels kept low-cardinality per CONVENTIONS § Labels autorisés.

cycles_total = Counter(
    "engine_cycles_total",
    "Total cycles completed by an engine, by terminal status.",
    ["engine", "status"],
)

cycle_duration = Histogram(
    "engine_cycle_duration_seconds",
    "Wall-clock duration of one engine cycle.",
    ["engine"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60, 120),
)

last_cycle_ts = Gauge(
    "engine_last_cycle_timestamp_seconds",
    "Unix timestamp of the last completed cycle. Used by alerts to detect "
    "stalled engines (now() - last_cycle_ts > N × cycle_period → STALE).",
    ["engine"],
)

ib_session_connected = Gauge(
    "ib_session_connected",
    "IB Gateway API session state per clientId (1=connected, 0=disconnected).",
    ["client_id"],
)

ib_requests_total = Counter(
    "ib_requests_total",
    "Total IB API requests made by engines, by type and outcome.",
    ["engine", "request_type", "status"],
)


# ── Observed cycle wrapper ───────────────────────────────────────────────────

@contextmanager
def observed_cycle(engine: str) -> Iterator[str]:
    """Wrap one engine cycle with cycle_id propagation + metrics + OTel span.

    Usage in an engine's main loop ::

        async def run_cycle(self) -> None:
            with observed_cycle("vol_engine") as cid:
                # work — logs auto-tagged with cycle_id=cid
                await self._fetch_chain()
                await self._calibrate()

    On exit, increments `engine_cycles_total{status="ok"}` and observes
    `engine_cycle_duration_seconds`. On exception, increments with
    `status="error"` and re-raises (so the engine's outer retry / restart
    logic still sees the failure).

    Emits two structlog events per cycle (`cycle_start` / `cycle_end`)
    with cycle_id + engine attached so the spec § 2.4 criterion "logs
    filterables via jq cycle_id" holds even if surrounding code uses
    stdlib logging.

    P2 : also creates the root OTel span ``<engine>_cycle`` for this
    cycle. Children spans created inside via
    ``tracer.start_as_current_span("stage_name")`` attach automatically
    (contextvars propagation). trace_id is bound to structlog so logs
    can be linked to traces in Grafana.
    """
    cid = new_cycle()
    log = structlog.get_logger()

    # P2 : open root OTel span. tracer is module-level (per-engine,
    # initialised by init_tracing() in main.py). Tolerate the case where
    # tracing is not initialised (unit tests, smoke scripts).
    try:
        from opentelemetry import trace
        tracer = trace.get_tracer(__name__)
        span_cm = tracer.start_as_current_span(
            f"{engine}_cycle",
            attributes={"engine": engine, "cycle_id": cid},
        )
    except ImportError:
        from contextlib import nullcontext
        span_cm = nullcontext()

    with span_cm as span:
        # Propagate trace_id to structlog so logs carry it (Loki ↔ Tempo
        # cross-navigation via Grafana's derivedFields).
        if span is not None:
            try:
                ctx = span.get_span_context()
                tid = format(ctx.trace_id, "032x")
                if tid != "0" * 32:
                    trace_id_var.set(tid)
                    structlog.contextvars.bind_contextvars(trace_id=tid)
            except Exception:
                pass

        log.info("cycle_start", engine=engine)
        start = time.perf_counter()
        status = "ok"
        try:
            yield cid
        except Exception as exc:
            status = "error"
            if span is not None:
                try:
                    span.record_exception(exc)
                    from opentelemetry.trace import Status, StatusCode
                    span.set_status(Status(StatusCode.ERROR))
                except Exception:
                    pass
            raise
        finally:
            duration = time.perf_counter() - start
            cycles_total.labels(engine=engine, status=status).inc()
            cycle_duration.labels(engine=engine).observe(duration)
            last_cycle_ts.labels(engine=engine).set(time.time())
            log.info(
                "cycle_end",
                engine=engine,
                status=status,
                duration_ms=round(duration * 1000, 3),
            )


# ── Metrics HTTP server ──────────────────────────────────────────────────────

def start_metrics_server(port: int) -> None:
    """Boot the Prometheus exposition endpoint on the given port.

    Idempotent in practice (prometheus_client raises OSError if the port
    is already bound — useful in tests to catch double-start). Each engine
    container exposes its own port :

      market-data : 9101
      vol-engine  : 9102
      risk-engine : 9103
      execution-engine : 9104
      db-writer   : 9105

    Spec : ``docs/LGTM_IMPLEMENTATION_SPEC.md`` § Phase 0 step 3.
    """
    start_http_server(port)


__all__ = [
    "cycle_id_var",
    "trace_id_var",
    "new_cycle",
    "clear_cycle",
    "observed_cycle",
    "cycles_total",
    "cycle_duration",
    "last_cycle_ts",
    "ib_session_connected",
    "ib_requests_total",
    "start_metrics_server",
]
