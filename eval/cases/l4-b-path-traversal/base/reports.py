import os

REPORTS_DIR = "/srv/shop/reports"


def report_names() -> list[str]:
    return sorted(os.listdir(REPORTS_DIR))
