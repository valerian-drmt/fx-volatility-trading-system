import os
import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
# R9 consolidates every package under src/ (PyPA src-layout): api, bus,
# core, engines, persistence, shared all import relative to src/. Only
# that single path needs to be on sys.path. PyQt5 was dropped in R8 PR #1
# so the qapp fixture and QT_QPA_PLATFORM setup are no longer needed.
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


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
