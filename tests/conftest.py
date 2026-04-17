# ruff: noqa: E402
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest_plugins = ["pytester"]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from PyQt5.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def pytest_collection_modifyitems(config, items):
    if os.environ.get("IB_RUN_INTEGRATION") == "1":
        return
    skip_integration = pytest.mark.skip(
        reason="integration tests require IB_RUN_INTEGRATION=1 and a live IB Gateway"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
