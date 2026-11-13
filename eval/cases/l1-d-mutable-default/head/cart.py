def cart_size(cart: list[str]) -> int:
    return len(cart)


def add_item(item: str, cart: list[str] = []) -> list[str]:
    """Add an item; without a cart, start a new one."""
    cart.append(item)
    return cart
