"""DTOs for the /api/v1/admin/config endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from core.config import VolTradingConfig


class ConfigResponse(BaseModel):
    """Current or historical config row with its metadata."""

    version: int = Field(description="Monotonic version number ; 0 = pydantic defaults (empty table)")
    config: VolTradingConfig
    updated_at: datetime
    updated_by: str | None = None
    comment: str | None = None


class ConfigPatchRequest(BaseModel):
    """Partial update. The `patch` object is deep-merged into the current config."""

    patch: dict[str, Any] = Field(description="Nested dict mirroring VolTradingConfig sections")
    user: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=500)


class ConfigRevertRequest(BaseModel):
    """Revert the current config to a past version (duplicates it as the new head)."""

    user: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=500)
