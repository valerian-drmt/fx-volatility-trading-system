"""Write the FastAPI OpenAPI schema to a JSON file without spinning uvicorn.

Used by CI to feed ``openapi-typescript`` for the frontend drift check
(`npm run gen:api:check` locally hits uvicorn, this script hits the app
directly).

Usage:
    python scripts/dump_openapi.py path/to/openapi.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "src"):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from api.main import create_app  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("openapi.json")
    schema = create_app().openapi()
    out.write_text(json.dumps(schema, indent=2))
    print(f"OpenAPI schema written to {out} ({len(schema['paths'])} paths)")


if __name__ == "__main__":
    main()
