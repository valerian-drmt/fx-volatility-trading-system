from enum import IntEnum


class ClientRole(IntEnum):
    ORDER_WORKER = 1
    MARKET_DATA = 2
    DASHBOARD = 3


def default_client_roles() -> dict[str, int]:
    return {
        "order_worker": int(ClientRole.ORDER_WORKER),
        "market_data": int(ClientRole.MARKET_DATA),
        "dashboard": int(ClientRole.DASHBOARD),
    }
