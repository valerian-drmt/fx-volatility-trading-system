"""Read-only analysis path to the prod database (runbooks section 5).

The role can only read, the port is loopback-only, the deploy never sends the
password in clear, the SCRAM verifier is the one Postgres computes, and the
access exists for a paper account only.
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


def _deploy_gate() -> str:
    deploy = (ROOT / "infrastructure" / "ec2" / "remote-deploy.sh").read_text(encoding="utf-8")
    return deploy.split("set_reader_login() {", 1)[1].split("\n}\n", 1)[0]


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
    gate = _deploy_gate()
    assert "ssm /fxvol/prod/DB_READONLY_PASSWORD" in gate
    assert "python3 infrastructure/ec2/scram_verifier.py" in gate
    assert "ALTER ROLE fxvol_reader WITH NOLOGIN PASSWORD NULL" in gate
    # the clear password is only ever piped to the verifier, never to psql or .env
    assert "DB_READONLY_PASSWORD=" not in deploy
    assert "PASSWORD '%s';\\n\" \"$verifier\"" in gate
    assert "set_reader_login ||" in deploy, "a failure here must not fail the deploy"


def test_deploy_grants_the_login_for_a_paper_account_only() -> None:
    gate = _deploy_gate()
    # both the configured mode and the account the gateway reported must be paper
    assert "SELECT account_type FROM runtime_ib_session WHERE broker = 'IB'" in gate
    assert '[ "$configured_mode" != "paper" ] || [ "$account_type" != "paper" ]' in gate
    refusal = gate.index('ro_pw=""')
    assert gate.index("configured_mode=") < refusal < gate.index('if [ -n "$ro_pw" ]'), (
        "the password is dropped before the branch that grants LOGIN"
    )


def test_client_refuses_anything_but_a_paper_account() -> None:
    client = _load(ROOT / "scripts" / "local" / "prod_db.py")
    assert client.paper_only("paper") is None
    for other in ("live", None, "", "PAPER", "demo"):
        refusal = client.paper_only(other)
        assert refusal and "PAPER account only" in refusal, other
    source = (ROOT / "scripts" / "local" / "prod_db.py").read_text(encoding="utf-8")
    body = source.split("async def run", 1)[1]
    assert body.index("paper_only(") < body.index("conn.cursor(sql)"), "the check runs before the query"
    assert "readonly=True" in body
    # the password is never printed nor passed on a command line
    assert "print(password" not in source and '"--password"' not in source


def test_reader_role_migration_grants_select_only() -> None:
    text = (ROOT / "src/persistence/migrations/versions/064_reader_role.py").read_text(encoding="utf-8")
    body = text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    grants = [line for line in body.splitlines() if "GRANT" in line]
    assert grants and all(
        ("SELECT" in g) or ("USAGE ON SCHEMA" in g) or ("CONNECT ON DATABASE" in g) for g in grants
    ), grants
    for forbidden in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALL PRIVILEGES", "SUPERUSER", "CREATEROLE"):
        assert forbidden not in body, forbidden
    assert "default_transaction_read_only = on" in body and "statement_timeout = '30s'" in body
    assert "NOLOGIN" in body


def test_the_gates_read_the_table_the_model_maps_to() -> None:
    """The first deploy read a table name that no longer exists and stayed closed."""
    models = (ROOT / "src" / "persistence" / "models.py").read_text(encoding="utf-8")
    after_class = models.split("class IbConnectionState(Base):", 1)[1]
    table = after_class.split('__tablename__ = "', 1)[1].split('"', 1)[0]
    client = _load(ROOT / "scripts" / "local" / "prod_db.py")
    assert f"FROM {table} " in client.ACCOUNT_TYPE_SQL
    assert _deploy_gate().count(f"FROM {table} WHERE broker = 'IB'") == 2
