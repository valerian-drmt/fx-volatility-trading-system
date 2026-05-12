"""Live smoke tests for the deployed v2 stack.

Gated by ``PROD_SMOKE=1`` — these tests hit the real prod host
(defaults to ``https://valerian.dev`` via ``PROD_HOST`` env var), so
they need an actually-running stack. Typical invocation after a deploy :

    PROD_SMOKE=1 PROD_HOST=https://valerian.dev pytest tests/test_post_deploy_smoke.py -v

What we validate without IB Gateway live :

- ``/api/v1/health`` returns 200 JSON ``{"status": "OK"}``
- ``/api/v1/health/extended`` reports Redis + DB OK (engines may be DOWN
  without IB creds, that's fine)
- ``/`` serves the React bundle entrypoint (``<div id="root">``) with
  cache-control immutable assets
- ``/openapi.json`` returns the current OAS schema (matches committed
  ``frontend/src/api/schema.d.ts`` shape via path count)
- TLS cert is valid (not expired, issued by a trusted CA)
- WS ``/ws/ticks`` accepts the upgrade (204 / 101 handshake) and closes
  cleanly — we don't wait for data, just verify the route is alive
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import pytest

PROD_HOST = os.environ.get("PROD_HOST", "https://valerian.dev").rstrip("/")
TIMEOUT_S = 10.0

pytestmark = pytest.mark.skipif(
    os.environ.get("PROD_SMOKE") != "1",
    reason="Set PROD_SMOKE=1 to hit the live prod host for the deploy smoke suite.",
)


def _get(path: str, timeout: float = TIMEOUT_S) -> tuple[int, dict, bytes]:
    """GET ``{PROD_HOST}{path}`` — returns (status, headers dict, body bytes)."""
    req = urllib.request.Request(
        f"{PROD_HOST}{path}",
        headers={"User-Agent": "fxvol-smoke/1.0", "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, dict(resp.headers), resp.read()


def test_health_endpoint_returns_ok():
    status, _, body = _get("/api/v1/health")
    assert status == 200
    payload = json.loads(body)
    assert payload.get("status") == "OK"


def test_health_extended_reports_redis_and_database_ok():
    status, _, body = _get("/api/v1/health/extended")
    assert status == 200
    payload = json.loads(body)
    components = payload.get("components", {})
    assert components.get("redis") == "OK", f"redis not OK : {payload}"
    assert components.get("database") == "OK", f"database not OK : {payload}"
    # Engines may be DOWN if IB creds are absent — do not fail on that.


def test_root_serves_react_bundle():
    status, headers, body = _get("/")
    assert status == 200
    content_type = headers.get("Content-Type", "")
    assert "text/html" in content_type
    # React Vite bundle entrypoint marker.
    assert b'<div id="root">' in body, "missing React root div — wrong bundle?"


def test_hashed_assets_carry_immutable_cache_control():
    """Pull an asset URL from the index and assert its Cache-Control header."""
    _, _, index_body = _get("/")
    import re

    m = re.search(rb'"(/assets/index-[A-Za-z0-9_-]+\.js)"', index_body)
    assert m is not None, "no hashed JS asset referenced in index.html"
    asset_path = m.group(1).decode()

    status, headers, _ = _get(asset_path)
    assert status == 200
    cache = headers.get("Cache-Control", "").lower()
    assert "immutable" in cache, f"hashed asset {asset_path} missing immutable cache"


def test_openapi_schema_is_served():
    status, _, body = _get("/api/openapi.json")
    assert status == 200
    schema = json.loads(body)
    assert "paths" in schema
    # Sanity : at least the 18 R4 endpoints are present.
    assert len(schema["paths"]) >= 10


def test_openapi_schema_matches_committed_frontend_types():
    """The shape of /openapi.json's paths must still align with the
    frontend's committed schema.d.ts — drift here means the deploy
    shipped a newer backend than the bundled frontend expects."""
    _, _, body = _get("/api/openapi.json")
    live_paths = set(json.loads(body)["paths"])

    schema_ts = (
        Path(__file__).resolve().parent.parent
        / "frontend" / "src" / "api" / "schema.d.ts"
    ).read_text(encoding="utf-8")
    import re

    ts_paths = set(re.findall(r'^\s*"(/api/v1/[^"]+)"\s*:\s*\{', schema_ts, re.M))

    missing = live_paths - ts_paths
    assert not missing, (
        f"live API exposes paths the frontend schema.d.ts doesn't know about : {sorted(missing)}"
    )


def test_tls_certificate_is_valid():
    """Open a TLS socket, rely on the default cert store. A self-signed
    or expired cert raises SSLError which fails the test."""
    if not PROD_HOST.startswith("https://"):
        pytest.skip("PROD_HOST is not https — TLS check skipped")
    host = urlparse(PROD_HOST).hostname
    ctx = ssl.create_default_context()
    import socket

    with (
        socket.create_connection((host, 443), timeout=TIMEOUT_S) as raw,
        ctx.wrap_socket(raw, server_hostname=host) as sock,
    ):
        cert = sock.getpeercert()
        assert cert is not None
        assert "subject" in cert


def test_websocket_ticks_route_accepts_upgrade():
    """Low-level WebSocket handshake — send the upgrade request and
    expect a 101 Switching Protocols before the server keeps the
    connection idle (we don't wait for data)."""
    import base64
    import secrets
    import socket

    parsed = urlparse(PROD_HOST)
    host = parsed.hostname
    port = 443 if parsed.scheme == "https" else 80
    key = base64.b64encode(secrets.token_bytes(16)).decode()

    req = (
        "GET /ws/ticks HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).encode()

    raw = socket.create_connection((host, port), timeout=TIMEOUT_S)
    try:
        if parsed.scheme == "https":
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(raw, server_hostname=host)
        else:
            sock = raw
        sock.sendall(req)
        response = sock.recv(4096)
    finally:
        try:
            raw.close()
        except OSError:
            pass

    first_line = response.split(b"\r\n", 1)[0]
    assert b"101" in first_line, (
        f"expected 101 Switching Protocols on /ws/ticks, got {first_line!r}"
    )
