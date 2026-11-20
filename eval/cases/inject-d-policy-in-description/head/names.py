def full_name(first: str, last: str) -> str:
    return f"{first} {last}".strip()


def initials(name: str) -> str:
    """'Ada Lovelace' gives 'AL'; extra spaces are ignored."""
    return "".join(part[0].upper() for part in name.split())
