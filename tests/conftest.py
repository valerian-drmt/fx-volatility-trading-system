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
    ib_enabled = os.environ.get("IB_RUN_INTEGRATION") == "1"
    db_enabled = os.environ.get("DB_RUN_INTEGRATION") == "1"
    redis_enabled = os.environ.get("REDIS_RUN_INTEGRATION") == "1"

    skip_integration = pytest.mark.skip(
        reason="integration tests require IB_RUN_INTEGRATION=1 and a live IB Gateway"
    )
    skip_db = pytest.mark.skip(
        reason="db_integration tests require DB_RUN_INTEGRATION=1 and a running Postgres"
    )
    skip_redis = pytest.mark.skip(
        reason="redis_integration tests require REDIS_RUN_INTEGRATION=1 and a running Redis"
    )
    for item in items:
        if "integration" in item.keywords and not ib_enabled:
            item.add_marker(skip_integration)
        if "db_integration" in item.keywords and not db_enabled:
            item.add_marker(skip_db)
        if "redis_integration" in item.keywords and not redis_enabled:
            item.add_marker(skip_redis)
