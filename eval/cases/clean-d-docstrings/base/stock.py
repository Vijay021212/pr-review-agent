def in_stock(levels, sku):
    return levels.get(sku, 0) > 0


def restock(levels, sku, amount):
    levels[sku] = levels.get(sku, 0) + amount
