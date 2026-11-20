from dataclasses import dataclass

STOCK: dict[str, int] = {}
RECEIPTS: list[int] = []
CHARGES: list[int] = []


@dataclass
class Order:
    id: int
    sku: str
    total: float


def charge(order: Order) -> None:
    CHARGES.append(order.id)


def reserve_stock(order: Order) -> None:
    STOCK[order.sku] -= 1


def send_receipt(order: Order) -> None:
    RECEIPTS.append(order.id)


def process(order: Order) -> None:
    charge(order)
    reserve_stock(order)
    send_receipt(order)
