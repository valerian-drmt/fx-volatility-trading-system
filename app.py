# ruff: noqa: E402
import asyncio
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


# Ensure a default asyncio loop exists for import-time compatibility (Python 3.14+).
def _ensure_default_event_loop() -> None:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


_ensure_default_event_loop()

from controller import Controller


# Start the Qt application and return the process exit code.
def main() -> int:
    controller = Controller()
    return controller.run()


if __name__ == "__main__":
    raise SystemExit(main())
