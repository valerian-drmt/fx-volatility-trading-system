#!/usr/bin/env python3
"""Password on stdin -> PostgreSQL SCRAM-SHA-256 verifier on stdout.

remote-deploy.sh sets the read-only role's password with the verifier, so the
clear text never reaches the server (no statement log, no error log can hold
it) and never sits in a process argument list. Standard library only: runs on
the host's python3.

    printf '%s' "$PW" | python3 scram_verifier.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys

ITERATIONS = 4096


def verifier(password: str, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    stored_key = hashlib.sha256(hmac.new(salted, b"Client Key", hashlib.sha256).digest()).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${ITERATIONS}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


if __name__ == "__main__":
    secret = sys.stdin.read().rstrip("\r\n")
    if not secret:
        sys.exit("scram_verifier: empty password on stdin")
    sys.stdout.write(verifier(secret))
