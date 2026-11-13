import os

REPORTS_DIR = "/srv/shop/reports"


def report_names() -> list[str]:
    return sorted(os.listdir(REPORTS_DIR))


def read_report(name: str) -> bytes:
    """The report file the owner asked for in the query string."""
    with open(os.path.join(REPORTS_DIR, name), "rb") as file:
        return file.read()
