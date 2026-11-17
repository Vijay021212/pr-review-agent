from dataclasses import dataclass


@dataclass
class Line:
    price: float
    quantity: int


def basket_total(lines: list[Line]) -> float:
    return sum((line.price * line.quantity for line in lines), 0.0)
