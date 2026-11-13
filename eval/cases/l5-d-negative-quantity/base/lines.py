from dataclasses import dataclass


@dataclass
class Line:
    price: float
    quantity: int

    @property
    def total(self) -> float:
        return self.price * self.quantity
