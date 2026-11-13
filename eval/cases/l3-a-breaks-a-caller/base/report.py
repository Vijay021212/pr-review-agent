from stats import average


def summary_line(values: list[float]) -> str:
    return f"Average: {average(values) * 100:.1f}%"
