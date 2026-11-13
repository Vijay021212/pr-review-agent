MAX_SCORE = 100


def letter(score: int) -> str:
    """90 or more is an A, 75 or more a B, anything lower a C."""
    if score > 90:
        return "A"
    if score >= 75:
        return "B"
    return "C"
