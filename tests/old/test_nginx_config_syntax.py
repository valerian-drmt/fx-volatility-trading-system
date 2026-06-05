"""Parse-level validation of the Nginx configs shipped in ``infrastructure/nginx/``.

Live ``nginx -t`` runs behind a Docker image in the CI job
(``nginx-config-test``). This test stays offline: it reads the files and
asserts the primitives we rely on (proxy_pass, Upgrade header, SPA fallback,
TLS) are present, so a typo or an accidental deletion is caught at pytest
time without needing a Docker daemon.
"""
from __future__ import annotations

from pathlib import Path

import pytest

CONF_DIR = Path(__file__).resolve().parent.parent / "infrastructure" / "nginx"


def _read(name: str) -> str:
    return (CONF_DIR / name).read_text(encoding="utf-8")


@pytest.mark.unit
def test_frontend_conf_has_spa_fallback_and_immutable_assets():
    conf = _read("frontend.conf")
    assert "listen 8080" in conf
    assert "try_files $uri $uri/ /index.html" in conf, "SPA fallback missing"
    assert "/assets/" in conf
    assert 'Cache-Control "public, immutable"' in conf
    assert "X-Content-Type-Options" in conf
    assert "X-Frame-Options" in conf


@pytest.mark.unit
def test_nginx_dev_routes_api_and_ws_upgrade():
    conf = _read("nginx-dev.conf")
    # Service hostnames are resolved at request time via Docker DNS so that
    # ``nginx -t`` doesn't fail at config-test time when the compose network
    # is absent (CI). We assert the variable + resolver pattern instead of
    # the static upstream block.
    assert "resolver 127.0.0.11" in conf, "Docker DNS resolver missing"
    assert 'set $api_upstream "api:8000"' in conf
    assert 'set $frontend_upstream "frontend:8080"' in conf
    assert "location /api/" in conf
    assert "location /ws/" in conf
    assert "Upgrade $http_upgrade" in conf, "WS upgrade header missing"
    assert 'Connection "upgrade"' in conf
    assert "proxy_read_timeout 3600s" in conf, "WS needs long read timeout"


@pytest.mark.unit
def test_nginx_prod_redirects_http_and_terminates_tls():
    conf = _read("nginx.conf")
    assert "listen 443 ssl" in conf
    assert "return 301 https://$host" in conf, "HTTP→HTTPS redirect missing"
    assert "ssl_protocols TLSv1.2 TLSv1.3" in conf
    assert "Strict-Transport-Security" in conf
    assert "limit_req_zone" in conf, "rate limit zone missing"
    assert "location /ws/" in conf
    assert "Upgrade $http_upgrade" in conf
