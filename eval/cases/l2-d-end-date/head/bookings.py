from datetime import date, timedelta

ONE_DAY = timedelta(days=1)


def booking_days(first: date, last: date) -> list[date]:
    """Every day from first to last, both included."""
    days = []
    day = first
    while day < last:
        days.append(day)
        day += ONE_DAY
    return days
