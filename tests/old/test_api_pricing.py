"""Tests for /api/v1/price, /greeks, /iv."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


@pytest.fixture
def client():
    fake_redis = AsyncMock()
    fake_redis.aclose = AsyncMock()
    with patch("api.main.aioredis.from_url", return_value=fake_redis), \
         TestClient(create_app()) as c:
        yield c


# ATM EURUSD option with 30d, 7.5% vol — textbook inputs.
_ATM = {
    "spot": 1.0850, "strike": 1.0850, "maturity_days": 30,
    "volatility": 0.075, "option_type": "CALL",
}


@pytest.mark.unit
class TestPriceRoute:
    def test_atm_call_returns_positive_price(self, client):
        r = client.post("/api/v1/price", json=_ATM)
        assert r.status_code == 200
        assert r.json()["price"] > 0

    def test_put_call_parity_on_atm(self, client):
        """For ATM (F=K), call and put prices should be equal (put-call parity with r=0)."""
        call = client.post("/api/v1/price", json=_ATM).json()["price"]
        put = client.post("/api/v1/price", json={**_ATM, "option_type": "PUT"}).json()["price"]
        assert abs(call - put) < 1e-9

    @pytest.mark.parametrize("bad_field,bad_value", [
        ("spot", 0), ("strike", -1), ("maturity_days", 0),
        ("volatility", 0), ("volatility", 10.0), ("option_type", "UNKNOWN"),
    ])
    def test_validation_rejects_bad_input(self, client, bad_field, bad_value):
        payload = {**_ATM, bad_field: bad_value}
        assert client.post("/api/v1/price", json=payload).status_code == 422


@pytest.mark.unit
class TestGreeksRoute:
    def test_full_response_shape(self, client):
        r = client.post("/api/v1/greeks", json=_ATM)
        body = r.json()
        assert r.status_code == 200
        assert set(body) == {"price", "delta", "gamma", "vega", "theta"}

    def test_atm_call_delta_close_to_half(self, client):
        """ATM call delta ≈ 0.5 (with zero drift)."""
        delta = client.post("/api/v1/greeks", json=_ATM).json()["delta"]
        assert 0.4 < delta < 0.6


@pytest.mark.unit
class TestImpliedVolRoute:
    def test_round_trip_price_then_iv(self, client):
        """Price at sigma=0.075, feed the price back → get sigma=0.075 back."""
        price_res = client.post("/api/v1/price", json=_ATM).json()
        iv_req = {
            "spot": _ATM["spot"], "strike": _ATM["strike"],
            "maturity_days": _ATM["maturity_days"],
            "market_price": price_res["price"],
            "option_type": _ATM["option_type"],
        }
        iv = client.post("/api/v1/iv", json=iv_req).json()["implied_volatility"]
        assert abs(iv - _ATM["volatility"]) < 1e-4

    def test_unreachable_market_price_returns_422(self, client):
        """A market_price greater than the forward is impossible for a call on this strike."""
        payload = {
            "spot": 1.0850, "strike": 1.0850, "maturity_days": 30,
            "market_price": 2.0,  # > forward → no valid sigma exists
            "option_type": "CALL",
        }
        r = client.post("/api/v1/iv", json=payload)
        assert r.status_code == 422
        assert "not solvable" in r.json()["detail"].lower()
