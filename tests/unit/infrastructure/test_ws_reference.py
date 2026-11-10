"""The WebSocket reference (#352) is generated from the canonical payloads and
lists every socket the api serves."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_reference_is_current() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "docs" / "gen_ws_reference.py"), "--check"],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stdout + out.stderr


def test_every_socket_is_documented() -> None:
    served = set(re.findall(r'@router\.websocket\("([^"]+)"\)', (ROOT / "src/api/routers/ws.py").read_text(encoding="utf-8")))
    doc = (ROOT / "docs/architecture/websockets.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^### `([^`]+)`", doc, re.M))
    assert served == documented, (served - documented, documented - served)
