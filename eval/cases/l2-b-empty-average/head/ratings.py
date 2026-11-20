def rating_count(ratings: list[int]) -> int:
    return len(ratings)


def average_rating(ratings: list[int]) -> float:
    """The mean rating shown on the product page."""
    return sum(ratings) / len(ratings)
