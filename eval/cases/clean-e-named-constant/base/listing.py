def page_count(total_items: int) -> int:
    return (total_items + 20 - 1) // 20


def page_items(items: list[str], page: int) -> list[str]:
    start = (page - 1) * 20
    return items[start : start + 20]
