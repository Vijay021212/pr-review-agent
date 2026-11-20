OUTBOX: list[tuple[str, str, str]] = []


def send(to: str, subject: str, body: str) -> None:
    OUTBOX.append((to, subject, body))
