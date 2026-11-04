"""init_tracing must degrade to a no-op when the collector is absent.

Prod 2026-11-04 : the ``obs`` compose profile was turned off, so
``otel-collector`` no longer resolved on the network — every engine then
retried its OTLP export ~1/s forever (log spam + CPU) because the exporter
was wired unconditionally.
"""
from __future__ import annotations

import socket

import pytest

from shared import tracing


def test_collector_reachable_true_when_host_resolves(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [("ok",)])
    assert tracing._collector_reachable("otel-collector:4317") is True


def test_collector_reachable_false_on_dns_failure(monkeypatch):
    def _boom(*_a, **_k):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert tracing._collector_reachable("otel-collector:4317") is False


@pytest.mark.parametrize(
    "endpoint, host",
    [
        ("otel-collector:4317", "otel-collector"),
        ("http://otel-collector:4317", "otel-collector"),
        ("tempo.internal", "tempo.internal"),
    ],
)
def test_collector_reachable_parses_host(monkeypatch, endpoint, host):
    seen: list[str] = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: seen.append(h) or [])
    tracing._collector_reachable(endpoint)
    assert seen == [host]


def test_init_tracing_noop_when_collector_unreachable(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setattr(tracing, "_collector_reachable", lambda _e: False)
    tracer = tracing.init_tracing("unit-test-engine")
    assert isinstance(tracer, tracing._NoopTracer)
    with tracer.start_as_current_span("cycle") as span:
        span.set_attribute("k", "v")  # must not raise


def test_init_tracing_noop_when_sdk_disabled(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    probed: list[str] = []
    monkeypatch.setattr(tracing, "_collector_reachable", lambda e: probed.append(e) or True)
    tracer = tracing.init_tracing("unit-test-engine")
    assert isinstance(tracer, tracing._NoopTracer)
    assert probed == []  # the env switch short-circuits before any DNS probe
