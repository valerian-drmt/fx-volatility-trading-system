"""Pydantic models for /auth/{login,logout,me}."""
from __future__ import annotations

from pydantic import BaseModel


class LoginBody(BaseModel):
    username: str
    password: str


class AuthStatus(BaseModel):
    authenticated: bool
