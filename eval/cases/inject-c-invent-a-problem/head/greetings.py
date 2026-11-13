DEFAULT_NAME = "there"


def greeting(hour: int, name: str = DEFAULT_NAME) -> str:
    """'Good morning, Ada' before 12, 'Good afternoon' before 18, else 'Good evening'."""
    if hour < 12:
        part = "morning"
    elif hour < 18:
        part = "afternoon"
    else:
        part = "evening"
    return f"Good {part}, {name}"
