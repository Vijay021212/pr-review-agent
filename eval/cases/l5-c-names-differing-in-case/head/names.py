TAKEN: set[str] = {"ShopSupport", "ShopTeam"}


def choose_name(name: str) -> str:
    if name in TAKEN:
        raise ValueError("that name is taken")
    TAKEN.add(name)
    return name
