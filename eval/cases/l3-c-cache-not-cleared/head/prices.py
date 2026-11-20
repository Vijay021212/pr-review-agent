_prices: dict[str, float] = {}
_cache: dict[str, float] = {}


def price_of(sku: str) -> float:
    if sku not in _cache:
        _cache[sku] = _prices[sku]
    return _cache[sku]


def set_price(sku: str, price: float) -> None:
    if price < 0:
        raise ValueError(f"invalid price {price}")
    _prices[sku] = price
