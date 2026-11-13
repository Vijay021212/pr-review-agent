PAGE_SIZE = 20


def page_of(orders: list[dict], page: int) -> list[dict]:
    """The orders on one page; pages are numbered from 1."""
    start = page * PAGE_SIZE
    return orders[start : start + PAGE_SIZE]
