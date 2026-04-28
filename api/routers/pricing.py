"""POST /api/v1/{price,greeks,iv} — thin BS pricing router."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.models.pricing import (
    GreeksRequest,
    GreeksResponse,
    ImpliedVolRequest,
    ImpliedVolResponse,
    PriceRequest,
    PriceResponse,
)
from api.services.pricing_service import (
    compute_greeks,
    compute_implied_vol,
    compute_price,
)

router = APIRouter(prefix="/api/v1", tags=["pricing"])


@router.post("/price", response_model=PriceResponse)
def price(req: PriceRequest) -> PriceResponse:
    return compute_price(req)


@router.post("/greeks", response_model=GreeksResponse)
def greeks(req: GreeksRequest) -> GreeksResponse:
    return compute_greeks(req)


@router.post("/iv", response_model=ImpliedVolResponse)
def implied_vol(req: ImpliedVolRequest) -> ImpliedVolResponse:
    """Invert BS → implied vol. 422 if market_price falls outside the bracket."""
    try:
        return compute_implied_vol(req)
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Implied vol not solvable in [1e-6, 5.0] : {e}",
        ) from e
