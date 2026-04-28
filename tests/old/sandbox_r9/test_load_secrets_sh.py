"""Contract test for scripts/load_secrets.sh: shim the aws CLI and assert
the script produces a valid EnvironmentFile for systemd with the 5 SSM
params + DATABASE_URL + REDIS_URL derived lines.

Skipped on systems without bash + jq (needs git-bash on Windows)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "load_secrets.sh"


def _find_posix_bash() -> str | None:
    """Return a bash that understands Windows paths (git-bash / msys2).

    WSL bash looks for /bin/bash inside the WSL root filesystem and cannot
    execute a script given via a Windows path, so we skip tests when only
    WSL bash is on PATH.
    """
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\msys64\usr\bin\bash.exe",
        "/usr/bin/bash",
        "/bin/bash",
    ):
        if Path(candidate).exists():
            return candidate
    found = shutil.which("bash")
    # Exclude WSL bash (System32\bash.exe) which cannot read Windows paths.
    if found and "System32" not in found and "SysWOW64" not in found:
        return found
    return None


BASH = _find_posix_bash()

pytestmark = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None,
    reason="posix bash (git-bash) and jq are required",
)


def _write_aws_shim(bin_dir: Path) -> Path:
    """Create an executable that mimics `aws ssm get-parameters --output json`."""
    payload = {
        "Parameters": [
            {"Name": "/fxvol/prod/IB_USERID", "Value": "stub-userid"},
            {"Name": "/fxvol/prod/IB_PASSWORD", "Value": "stub-password"},
            {"Name": "/fxvol/prod/DB_PASSWORD", "Value": "stub-db-pw"},
            {"Name": "/fxvol/prod/VNC_PASSWORD", "Value": "stub-vnc"},
            {"Name": "/fxvol/prod/TRADING_MODE", "Value": "paper"},
        ],
        "InvalidParameters": [],
    }
    shim = bin_dir / "aws"
    shim.write_text(f"#!/usr/bin/env bash\ncat <<'JSON'\n{json.dumps(payload)}\nJSON\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return shim


def test_load_secrets_sh_renders_env_file(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_aws_shim(bin_dir)
    out_file = tmp_path / "fxvol.env"

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FXVOL_ENV_OUT"] = str(out_file)
    env["FXVOL_SKIP_CHOWN"] = "1"

    result = subprocess.run(
        [BASH, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert out_file.exists()
    lines = out_file.read_text().splitlines()

    rendered = dict(line.split("=", 1) for line in lines if "=" in line)
    assert rendered["IB_USERID"] == "stub-userid"
    assert rendered["IB_PASSWORD"] == "stub-password"
    assert rendered["DB_PASSWORD"] == "stub-db-pw"
    assert rendered["VNC_PASSWORD"] == "stub-vnc"
    assert rendered["TRADING_MODE"] == "paper"
    assert rendered["DATABASE_URL"] == "postgresql+asyncpg://fxvol:stub-db-pw@postgres:5432/fxvol"
    assert rendered["REDIS_URL"] == "redis://redis:6379/0"


def test_load_secrets_sh_fails_on_invalid_params(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # aws shim that reports one of the params as missing in SSM
    shim = bin_dir / "aws"
    payload = {"Parameters": [], "InvalidParameters": ["/fxvol/prod/IB_USERID"]}
    shim.write_text(f"#!/usr/bin/env bash\ncat <<'JSON'\n{json.dumps(payload)}\nJSON\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FXVOL_ENV_OUT"] = str(tmp_path / "fxvol.env")
    env["FXVOL_SKIP_CHOWN"] = "1"

    result = subprocess.run(
        [BASH, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert "missing SSM params" in result.stderr
