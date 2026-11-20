PAGE_SIZE = 20


def page_count(total_items: int) -> int:
    return (total_items + PAGE_SIZE - 1) // PAGE_SIZE


def page_items(items: list[str], page: int) -> list[str]:
    start = (page - 1) * PAGE_SIZE
    return items[start : start + PAGE_SIZE]
