"""OpenAPI contract (#352, audit 11#10): every operation is described, the
cookie-gated ones declare `traderCookie` and a 401, every one documents 429,
error codes the endpoint raises are listed, and prod serves no Swagger UI."""
from __future__ import annotations

import pytest

from api.main import create_app


@pytest.fixture(scope="module")
def spec() -> dict:
    return create_app().openapi()


def _ops(spec: dict):
    for path, item in spec["paths"].items():
        for method, op in item.items():
            yield f"{method.upper()} {path}", op


def test_every_operation_is_described(spec: dict) -> None:
    missing = [name for name, op in _ops(spec) if not (op.get("description") or "").strip()]
    assert missing == []


def test_gated_operations_declare_the_cookie_and_a_401(spec: dict) -> None:
    assert spec["components"]["securitySchemes"]["traderCookie"] == {
        **spec["components"]["securitySchemes"]["traderCookie"], "type": "apiKey", "in": "cookie", "name": "fxvol_auth",
    }
    gated = {name: op for name, op in _ops(spec) if op.get("security")}
    assert "POST /api/v1/trade/submit" in gated and "PUT /api/v1/admin/config" in gated
    assert all("401" in op["responses"] for op in gated.values())
    assert "GET /api/v1/vol/surface" not in gated, "reads stay public"


def test_errors_are_documented(spec: dict) -> None:
    for name, op in _ops(spec):
        assert "429" in op["responses"], name
    submit = spec["paths"]["/api/v1/trade/submit"]["post"]["responses"]
    assert "409" in submit
    assert submit["409"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/ErrorResponse"


def test_prod_serves_the_spec_but_no_swagger_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", "x" * 64)
    monkeypatch.setenv("AUTH_SECRET", "s" * 48)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/x")
    app = create_app()
    assert app.docs_url is None and app.redoc_url is None and app.openapi_url == "/api/openapi.json"
