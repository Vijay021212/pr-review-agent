"""Tests for how a diff is shown to the model."""

from __future__ import annotations

from pr_review_agent.diff_text import estimate_tokens, render_file
from pr_review_agent.domain import ChangedFile, DiffLine, FileStatus, LineKind
from tests.builders import make_file


def test_lines_carry_their_new_line_numbers() -> None:
    file = ChangedFile(
        path="src/stats.py",
        status=FileStatus.MODIFIED,
        lines=(
            DiffLine(kind=LineKind.CONTEXT, old_number=1, new_number=1, text="def average(xs):"),
            DiffLine(kind=LineKind.REMOVED, old_number=2, text="    return sum(xs) / len(xs)"),
            DiffLine(kind=LineKind.ADDED, new_number=2, text="    if not xs:"),
        ),
    )
    assert render_file(file) == (
        "### src/stats.py (modified)\n"
        "     1   def average(xs):\n"
        "       -     return sum(xs) / len(xs)\n"
        "     2 +     if not xs:"
    )


def test_the_header_says_what_happened_to_the_file() -> None:
    assert render_file(make_file("a.py", status=FileStatus.ADDED)).startswith("### a.py (new file)")
    assert "(deleted)" in render_file(make_file("a.py", (), status=FileStatus.REMOVED))
    renamed = make_file("b.py", (), status=FileStatus.RENAMED, previous_path="a.py")
    assert render_file(renamed) == "### b.py (renamed from a.py)\n       (no line changes)"


def test_tokens_are_overestimated_at_three_characters_each() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("abcd") == 2
    assert estimate_tokens("x" * 300) == 100
