def in_stock(levels: dict[str, int], sku: str) -> bool:
    """True when at least one item of `sku` is in stock."""
    return levels.get(sku, 0) > 0


def restock(levels: dict[str, int], sku: str, amount: int) -> None:
    """Add `amount` items of `sku` to the stock levels."""
    levels[sku] = levels.get(sku, 0) + amount
