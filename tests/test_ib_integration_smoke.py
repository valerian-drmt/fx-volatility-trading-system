import os

import pytest
from ib_insync import IB

from services.ib_client import IBClient


@pytest.mark.integration
def test_ib_paper_connection_smoke():
    if os.getenv("IB_RUN_INTEGRATION") != "1":
        pytest.skip("Set IB_RUN_INTEGRATION=1 to run IB paper integration tests.")

    host = str(os.getenv("IB_HOST", "")).strip()
    port_raw = str(os.getenv("IB_PORT", "")).strip()
    client_id_raw = str(os.getenv("IB_CLIENT_ID", "9001")).strip()

    if not host or not port_raw:
        pytest.skip("IB_HOST and IB_PORT are required for integration tests.")

    try:
        port = int(port_raw)
        client_id = int(client_id_raw)
    except ValueError:
        pytest.skip("IB_PORT and IB_CLIENT_ID must be valid integers.")

    ib = IB()
    client = IBClient(
        ib=ib,
        host=host,
        port=port,
        client_id=client_id,
        readonly=True,
    )
    try:
        connected = client.connect(timeout=3.0)
        assert connected, client.get_last_error_text() or "Unable to connect to IB Gateway/TWS."
        status = client.get_status_snapshot()
        assert status["connected"] is True
    finally:
        if ib.isConnected():
            ib.disconnect()
