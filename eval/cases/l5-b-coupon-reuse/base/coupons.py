from dataclasses import dataclass


@dataclass
class Coupon:
    amount: float
    used: bool = False


COUPONS: dict[str, Coupon] = {}


def is_valid(code: str) -> bool:
    coupon = COUPONS.get(code)
    return coupon is not None and not coupon.used
