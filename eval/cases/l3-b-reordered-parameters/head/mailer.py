OUTBOX: list[tuple[str, str, str]] = []


def send(subject: str, to: str, body: str) -> None:
    OUTBOX.append((to, subject, body))
