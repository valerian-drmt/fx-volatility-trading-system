"""Pydantic models for /auth/{login,logout,me}."""
from __future__ import annotations

from pydantic import BaseModel, Field


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AuthStatus(BaseModel):
    authenticated: bool
