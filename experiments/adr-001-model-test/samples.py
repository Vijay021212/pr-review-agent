"""
Test pull requests for the ADR-001 model test.

Each sample is a tiny pull request: a title, a description, the files before and
after the change, and (for some) related files the change affects. Four samples
contain ONE planted bug each; the fifth is clean, so we can count false alarms.

The expected bug's line is found by a marker string (a piece of text on that line)
instead of a hard-coded number, so editing a sample can't silently break the answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FileChange:
    """One file in the pull request, before and after the change ("" = new file)."""

    path: str
    old: str
    new: str


@dataclass(frozen=True)
class PlantedBug:
    """The answer key: where the bug is and what it is (for the person reading the results)."""

    path: str
    line_marker: str  # text that appears on the buggy line of the NEW file
    level: int  # review level from requirements section 5.1
    what: str
    # Another correct place to report it, e.g. the caller in a related file where the crash happens.
    also_path: str | None = None
    also_marker: str | None = None

    def line(self, changes: list[FileChange]) -> int:
        """1-based line number of the marker in the new version of the file."""
        new = next(change.new for change in changes if change.path == self.path)
        for number, text in enumerate(new.splitlines(), start=1):
            if self.line_marker in text:
                return number
        raise ValueError(f"Marker {self.line_marker!r} not found in {self.path}")


@dataclass(frozen=True)
class Sample:
    """One test pull request."""

    name: str
    title: str
    description: str
    changes: list[FileChange]
    related: dict[str, str] = field(default_factory=dict)  # unchanged files the change affects
    bug: PlantedBug | None = None  # None = a clean pull request


SAMPLES = [
    # Level 1-2: an obvious missing None check (dict.get returns None for unknown ids).
    Sample(
        name="01-missing-none-check",
        title="Add helper to look up a user's email",
        description="Adds get_user_email() so the notification code can find where to send emails.",
        changes=[
            FileChange(
                path="users.py",
                old="",
                new='''def get_user_email(users, user_id):
    """Return the user's email address in lower case."""
    user = users.get(user_id)
    return user["email"].lower()
''',
            )
        ],
        bug=PlantedBug(
            path="users.py",
            line_marker='return user["email"]',
            level=1,
            what="users.get() returns None for an unknown id, so user['email'] raises TypeError.",
        ),
    ),
    # Level 2: an off-by-one error that contradicts the function's own docstring.
    Sample(
        name="02-off-by-one-page",
        title="Add pagination for the order list",
        description="Adds paginate() so the order page can show 20 orders at a time. Pages start at 1.",
        changes=[
            FileChange(
                path="pagination.py",
                old="",
                new='''def paginate(items, page, page_size):
    """Return the items on a page. Pages are numbered from 1."""
    start = page * page_size
    end = start + page_size
    return items[start:end]
''',
            )
        ],
        bug=PlantedBug(
            path="pagination.py",
            line_marker="start = page * page_size",
            level=2,
            what="Pages start at 1, so start must be (page - 1) * page_size; page 1 skips the first page.",
        ),
    ),
    # Level 3: the change itself looks fine, but it breaks a caller in another file.
    Sample(
        name="03-breaks-a-caller",
        title="Stop average() crashing on an empty list",
        description="average([]) used to raise ZeroDivisionError. It now returns None for an empty list.",
        changes=[
            FileChange(
                path="stats.py",
                old='''def average(numbers):
    """Return the mean of the numbers."""
    return sum(numbers) / len(numbers)
''',
                new='''def average(numbers):
    """Return the mean of the numbers, or None when the list is empty."""
    if not numbers:
        return None
    return sum(numbers) / len(numbers)
''',
            )
        ],
        related={
            "report.py": '''from stats import average


def score_line(student, scores):
    """One line of the class report, e.g. "Ana: 81.5"."""
    return f"{student}: {round(average(scores), 1)}"
''',
        },
        bug=PlantedBug(
            path="stats.py",
            line_marker="return None",
            level=3,
            what="report.py calls round(average(scores), 1); round(None) raises TypeError for a student with no scores.",
            also_path="report.py",
            also_marker="round(average(scores), 1)",
        ),
    ),
    # Level 4: a security problem (SQL injection through string formatting).
    Sample(
        name="04-sql-injection",
        title="Add order search by customer name",
        description="Support staff can now search orders by typing a customer's name.",
        changes=[
            FileChange(
                path="orders.py",
                old="",
                new='''def find_orders(conn, customer_name):
    """Return (id, total) for every order placed by the named customer."""
    query = f"SELECT id, total FROM orders WHERE customer = '{customer_name}'"
    return conn.execute(query).fetchall()
''',
            )
        ],
        bug=PlantedBug(
            path="orders.py",
            line_marker="query = f\"SELECT",
            level=4,
            what="customer_name is put straight into the SQL text, so input like ' OR '1'='1 reads every order (SQL injection).",
        ),
    ),
    # Clean: a correct change with a test. Any Medium-or-worse finding here is a false alarm.
    Sample(
        name="05-clean",
        title="Add clamp() helper",
        description="Adds clamp() to keep slider values inside their allowed range, with tests.",
        changes=[
            FileChange(
                path="ranges.py",
                old="",
                new='''def clamp(value, low, high):
    """Limit value to the range low..high (both included)."""
    if low > high:
        raise ValueError("low must not be greater than high")
    return max(low, min(value, high))
''',
            ),
            FileChange(
                path="test_ranges.py",
                old="",
                new='''import pytest

from ranges import clamp


def test_clamp_inside_range():
    assert clamp(5, 0, 10) == 5


def test_clamp_below_and_above():
    assert clamp(-3, 0, 10) == 0
    assert clamp(42, 0, 10) == 10


def test_clamp_rejects_reversed_range():
    with pytest.raises(ValueError):
        clamp(1, 10, 0)
''',
            ),
        ],
    ),
]
