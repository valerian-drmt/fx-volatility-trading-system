"""Covers the IB_HOST / IB_PORT override introduced in R6 PR #4.

The dockerised IB Gateway lives at ``ib-gateway:4002`` inside the compose
network but the host-side PyQt app keeps connecting to ``127.0.0.1:4002``
(the port-forwarded bind). Both paths must work from the same code —
``_default_ib_host`` / ``_default_ib_port`` read the env, ``IBClient``
falls back to them when no explicit value is passed.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from services.ib_client import IBClient, _default_ib_host, _default_ib_port


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("IB_HOST", raising=False)
    monkeypatch.delenv("IB_PORT", raising=False)


@pytest.mark.unit
def test_default_host_is_localhost_when_env_unset():
    assert _default_ib_host() == "127.0.0.1"


@pytest.mark.unit
def test_host_picks_up_ib_host_env(monkeypatch):
    monkeypatch.setenv("IB_HOST", "ib-gateway")
    assert _default_ib_host() == "ib-gateway"


@pytest.mark.unit
def test_default_port_is_4002_when_env_unset():
    assert _default_ib_port() == 4002


@pytest.mark.unit
def test_port_picks_up_ib_port_env(monkeypatch):
    monkeypatch.setenv("IB_PORT", "4003")
    assert _default_ib_port() == 4003


@pytest.mark.unit
def test_ib_client_reads_env_when_host_arg_omitted(monkeypatch):
    monkeypatch.setenv("IB_HOST", "ib-gateway")
    monkeypatch.setenv("IB_PORT", "4003")
    client = IBClient(ib=MagicMock())
    assert client.host == "ib-gateway"
    assert client.port == 4003


@pytest.mark.unit
def test_ib_client_explicit_host_overrides_env(monkeypatch):
    monkeypatch.setenv("IB_HOST", "ib-gateway")
    client = IBClient(ib=MagicMock(), host="1.2.3.4", port=9999)
    assert client.host == "1.2.3.4"
    assert client.port == 9999


@pytest.mark.unit
def test_ib_client_empty_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("IB_HOST", "")
    monkeypatch.setenv("IB_PORT", "")
    # Empty string is treated as "not set" so default applies.
    assert _default_ib_host() == "127.0.0.1"
    assert _default_ib_port() == 4002


# Extend the compose test suite with ib-gateway invariants. Duplicated here
# rather than in test_docker_compose_syntax so the PR #4 scope lives in one
# module.
_COMPOSE_PATH = (
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/docker-compose.yml"
)


def _compose() -> dict:
    import yaml

    with open(_COMPOSE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.mark.unit
def test_ib_gateway_service_uses_expected_image():
    svc = _compose()["services"]["ib-gateway"]
    assert svc["image"].startswith("ghcr.io/unusualalpha/ib-gateway")


@pytest.mark.unit
def test_ib_gateway_binds_ports_to_localhost_only():
    svc = _compose()["services"]["ib-gateway"]
    # Critical : 4002 and 5900 must NOT be on 0.0.0.0 (LAN exposure).
    for p in svc["ports"]:
        assert str(p).startswith("127.0.0.1:"), (
            f"ib-gateway port {p} must bind 127.0.0.1 only — exposing "
            "IB Gateway API to the LAN is a credential leak"
        )


@pytest.mark.unit
def test_ib_gateway_on_internal_and_external_networks_only():
    svc = _compose()["services"]["ib-gateway"]
    assert set(svc["networks"]) == {"fxvol-internal", "fxvol-external"}


@pytest.mark.unit
def test_ib_gateway_paper_trading_default():
    env = _compose()["services"]["ib-gateway"]["environment"]
    assert env["TRADING_MODE"] == "paper", (
        "default to paper trading — live mode must be an explicit prod override"
    )


@pytest.mark.unit
def test_ib_gateway_is_opt_in_via_profile():
    """ib-gateway must not start on a plain `docker compose up` — it lives
    behind the `ib` profile so the default stack boots without a 4002 bind."""
    svc = _compose()["services"]["ib-gateway"]
    assert svc.get("profiles") == ["ib"], (
        "ib-gateway must be in the `ib` profile so it is opt-in"
    )
