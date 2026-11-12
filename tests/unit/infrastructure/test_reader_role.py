"""Read-only analysis path to the prod database (runbooks section 5).

The role can only read, the port is loopback-only, the deploy never sends the
password in clear, and the SCRAM verifier is the one Postgres computes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit


def _root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("repo root not found")


ROOT = _root()


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scram_verifier_matches_rfc_7677_derivation() -> None:
    scram = _load(ROOT / "infrastructure" / "ec2" / "scram_verifier.py")
    salt = bytes(range(16))
    out = scram.verifier("pencil", salt)
    scheme, rest = out.split("$", 1)
    params, keys = rest.split("$")
    iterations, salt_b64 = params.split(":")
    stored_b64, server_b64 = keys.split(":")
    assert scheme == "SCRAM-SHA-256" and iterations == "4096"
    assert base64.b64decode(salt_b64) == salt
    # A client that knows the password produces a proof the stored key accepts.
    salted = hashlib.pbkdf2_hmac("sha256", b"pencil", salt, 4096)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    assert hashlib.sha256(client_key).digest() == base64.b64decode(stored_b64)
    assert hmac.new(salted, b"Server Key", hashlib.sha256).digest() == base64.b64decode(server_b64)
    assert scram.verifier("pencil") != scram.verifier("pencil"), "random salt per call"


def test_postgres_is_published_on_the_host_loopback_only() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["postgres"]["ports"] == ["127.0.0.1:15432:5432"]


def test_deploy_sets_the_reader_login_from_a_verifier_and_closes_it_without_a_password() -> None:
    deploy = (ROOT / "infrastructure" / "ec2" / "remote-deploy.sh").read_text(encoding="utf-8")
    assert "ssm /fxvol/prod/DB_READONLY_PASSWORD" in deploy
    assert "python3 infrastructure/ec2/scram_verifier.py" in deploy
    assert "ALTER ROLE fxvol_reader WITH NOLOGIN PASSWORD NULL" in deploy
    # the clear password is only ever piped to the verifier, never to psql or .env
    assert "DB_READONLY_PASSWORD=" not in deploy
    assert 'PASSWORD \'%s\';\n" "$verifier"' in deploy
    assert "set_reader_login ||" in deploy, "a failure here must not fail the deploy"


def test_reader_role_migration_grants_select_only() -> None:
    text = (ROOT / "src/persistence/migrations/versions/064_reader_role.py").read_text(encoding="utf-8")
    body = text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    grants = [line for line in body.splitlines() if "GRANT" in line]
    assert grants and all(("SELECT" in g) or ("USAGE ON SCHEMA" in g) or ("CONNECT ON DATABASE" in g) for g in grants), grants
    for forbidden in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALL PRIVILEGES", "SUPERUSER", "CREATEROLE"):
        assert forbidden not in body, forbidden
    assert "default_transaction_read_only = on" in body and "statement_timeout = '30s'" in body
    assert "NOLOGIN" in body
