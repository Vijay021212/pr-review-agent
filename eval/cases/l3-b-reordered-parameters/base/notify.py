from mailer import send


def welcome(email: str, name: str) -> None:
    send(email, "Welcome", f"Hello {name}, thanks for joining.")
