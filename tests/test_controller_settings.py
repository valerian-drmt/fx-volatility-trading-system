import pytest

from controller import Controller


@pytest.mark.unit
def test_validate_status_settings_normalizes_values():
    normalized = Controller._validate_status_settings(
        {
            "host": " 127.0.0.1 ",
            "port": "4002",
            "client_id": "7",
            "readonly": False,
            "market_symbol": " eurusd ",
        }
    )

    assert normalized == {
        "host": "127.0.0.1",
        "port": 4002,
        "client_id": 7,
        "readonly": False,
        "market_symbol": "EURUSD",
    }


@pytest.mark.unit
def test_validate_status_settings_rejects_missing_keys():
    with pytest.raises(ValueError, match="Missing settings keys"):
        Controller._validate_status_settings({"host": "127.0.0.1"})


@pytest.mark.unit
def test_validate_status_settings_rejects_empty_host():
    with pytest.raises(ValueError, match="host"):
        Controller._validate_status_settings(
            {
                "host": "   ",
                "port": 4002,
                "client_id": 1,
                "readonly": True,
                "market_symbol": "EURUSD",
            }
        )


@pytest.mark.unit
def test_validate_status_settings_rejects_empty_market_symbol():
    with pytest.raises(ValueError, match="market_symbol"):
        Controller._validate_status_settings(
            {
                "host": "127.0.0.1",
                "port": 4002,
                "client_id": 1,
                "readonly": True,
                "market_symbol": "   ",
            }
        )


@pytest.mark.unit
def test_validate_app_settings_supports_status_payload():
    validated = Controller._validate_app_settings(
        {
            "status": {
                "host": "127.0.0.1",
                "port": 4002,
                "client_id": 2,
                "readonly": True,
                "market_symbol": "EURUSD",
            }
        }
    )

    assert validated["status"]["market_symbol"] == "EURUSD"
    assert validated["status"]["client_id"] == 2


@pytest.mark.unit
def test_validate_app_settings_supports_legacy_top_level_payload():
    validated = Controller._validate_app_settings(
        {
            "host": "127.0.0.1",
            "port": 4002,
            "client_id": 3,
            "readonly": True,
            "live_streaming": {"market_symbol": "GBPUSD"},
        }
    )

    assert validated["status"]["host"] == "127.0.0.1"
    assert validated["status"]["market_symbol"] == "GBPUSD"


@pytest.mark.unit
def test_validate_app_settings_rejects_non_object():
    with pytest.raises(ValueError, match="JSON object"):
        Controller._validate_app_settings([])
