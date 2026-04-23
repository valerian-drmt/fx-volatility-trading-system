"""Regression: api.routers.cockpit must import cleanly (was crashing boot with api.deps)."""
import importlib


def test_cockpit_router_imports():
    module = importlib.import_module("api.routers.cockpit")
    assert hasattr(module, "router"), "cockpit module must expose a FastAPI router"
    assert module.router.prefix == "/api/v1/vol"
