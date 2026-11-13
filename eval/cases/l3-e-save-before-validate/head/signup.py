ACCOUNTS: list[dict] = []


def validate(account: dict) -> None:
    if "@" not in account["email"]:
        raise ValueError("invalid email")


def save(account: dict) -> None:
    ACCOUNTS.append(account)


def sign_up(name: str, email: str) -> dict:
    account = {"name": name, "email": email.strip().lower()}
    save(account)
    validate(account)
    return account
