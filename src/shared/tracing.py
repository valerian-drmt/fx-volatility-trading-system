"""OTel tracing setup — P2 obs.

Initialises an OTLP gRPC exporter pointing at the otel-collector service.
Once `init_tracing(service_name)` is called from an engine's main, spans
created via `tracer.start_as_current_span(...)` flow → otel-collector
(:4317) → Tempo → Grafana.

The exporter endpoint defaults to ``otel-collector:4317`` inside the
fxvol-internal network ; override via env ``OTEL_EXPORTER_OTLP_ENDPOINT``.

Spec : docs/LGTM_IMPLEMENTATION_SPEC.md § Phase 2.

Compat notes :
- Engines use ib_async which calls ``nest_asyncio.apply()`` on its IB
  event loop. OTel spans propagate via ``contextvars`` which are
  asyncio-aware (3.7+), so the parent/child relationship survives
  ``await`` boundaries naturally. Do NOT auto-instrument asyncio
  (``opentelemetry-instrumentation-asyncio``) — it conflicts with
  nest_asyncio's monkey-patches.
- The exporter is gRPC + insecure (TLS off, dev-internal network only).
  Prod EC2 should switch to TLS + auth header.
"""
from __future__ import annotations

import logging
import socket
from typing import Any

logger = logging.getLogger(__name__)


def _collector_reachable(endpoint: str) -> bool:
    """True when the OTLP endpoint's host resolves.

    The obs compose profile (otel-collector + Tempo) is opt-in ; when it is
    off, the hostname does not exist on the compose network. Probing once at
    start-up lets the engines run without an exporter instead of retrying a
    DNS failure on every span batch forever (one warning per ~1s per engine,
    plus the CPU of the retry loop).
    """
    host = endpoint.split("://", 1)[-1].rsplit(":", 1)[0] or endpoint
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def init_tracing(service_name: str) -> Any:
    """Initialise OTel tracing for one engine process.

    Idempotent : a second call returns the existing TracerProvider rather
    than re-registering. Returns a ``Tracer`` instance ready for
    ``with tracer.start_as_current_span(...)``.

    Safe to call before any other tracing import — handles the case where
    OTel SDK isn't installed (e.g. minimal test env) by returning a no-op
    tracer that ignores all calls.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("otel_sdk_missing, tracing disabled for %s", service_name)
        return _NoopTracer()

    # Avoid re-registering if init_tracing is called twice (e.g. tests).
    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        return trace.get_tracer(service_name)

    from shared.config import get_settings

    settings = get_settings()
    if settings.otel_sdk_disabled:
        logger.info("otel_tracing_disabled service=%s (OTEL_SDK_DISABLED)", service_name)
        return _NoopTracer()
    endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT
    if not _collector_reachable(endpoint):
        logger.warning(
            "otel_collector_unreachable service=%s endpoint=%s — tracing disabled "
            "(start the `obs` compose profile to enable it)",
            service_name, endpoint,
        )
        return _NoopTracer()

    resource = Resource.create({
        "service.name": service_name,
        "service.namespace": "fxvol",
        "deployment.environment": settings.ENVIRONMENT,
    })
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    logger.info("otel_tracing_initialised service=%s endpoint=%s", service_name, endpoint)
    return trace.get_tracer(service_name)


class _NoopTracer:
    """Fallback used when opentelemetry packages aren't importable.

    Implements just the `start_as_current_span` contextmanager surface
    that `observed_cycle` and engine code expect, so the rest of the
    pipeline keeps working without tracing.
    """

    def start_as_current_span(self, name: str, **_kwargs: Any) -> Any:
        from contextlib import nullcontext
        return nullcontext(enter_result=_NoopSpan())


class _NoopSpan:
    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def set_attributes(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def record_exception(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def get_span_context(self) -> Any:
        return _NoopSpanContext()


class _NoopSpanContext:
    trace_id = 0
    span_id = 0


__all__ = ["init_tracing"]
