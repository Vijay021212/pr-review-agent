from dataclasses import dataclass


@dataclass
class Line:
    price: float
    quantity: int

    @property
    def total(self) -> float:
        return self.price * self.quantity


def set_quantity(line: Line, quantity: int) -> Line:
    """Called with the quantity the shopper typed on the cart page."""
    line.quantity = quantity
    return line
