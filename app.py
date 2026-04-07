# ruff: noqa: E402
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ib_insync import util

util.patchAsyncio()
util.useQt("PyQt5")

from controller import Controller


# Start the Qt application and return the process exit code.
def main() -> int:
    controller = Controller()
    return controller.run()


if __name__ == "__main__":
    raise SystemExit(main())
