"""Tests for reading GitHub's patch text into numbered diff lines."""

from __future__ import annotations

from pr_review_agent.domain import DiffLine, LineKind
from pr_review_agent.github.diff import parse_patch


def kinds_and_numbers(lines: tuple[DiffLine, ...]) -> list[tuple[str, int | None, int | None]]:
    return [(line.kind.value, line.old_number, line.new_number) for line in lines]


def test_a_hunk_with_context_removal_and_additions() -> None:
    patch = (
        "@@ -10,3 +10,4 @@ def total(items):\n"
        "     count = 0\n"
        "-    return sum(items)\n"
        "+    if not items:\n"
        "+        return 0\n"
        "     # end"
    )
    lines = parse_patch(patch)
    assert kinds_and_numbers(lines) == [
        ("context", 10, 10),
        ("removed", 11, None),
        ("added", None, 11),
        ("added", None, 12),
        ("context", 12, 13),
    ]
    assert lines[1].text == "    return sum(items)"
    assert lines[2].text == "    if not items:"


def test_several_hunks_each_start_at_their_header() -> None:
    patch = "@@ -1,1 +1,1 @@\n-a\n+b\n@@ -40,2 +40,3 @@\n x\n+y\n z"
    assert kinds_and_numbers(parse_patch(patch)) == [
        ("removed", 1, None),
        ("added", None, 1),
        ("context", 40, 40),
        ("added", None, 41),
        ("context", 41, 42),
    ]


def test_a_new_file() -> None:
    lines = parse_patch("@@ -0,0 +1,2 @@\n+first\n+second")
    assert kinds_and_numbers(lines) == [("added", None, 1), ("added", None, 2)]


def test_a_deleted_file() -> None:
    lines = parse_patch("@@ -1,2 +0,0 @@\n-first\n-second")
    assert kinds_and_numbers(lines) == [("removed", 1, None), ("removed", 2, None)]


def test_a_count_of_one_can_be_left_out_of_the_header() -> None:
    assert kinds_and_numbers(parse_patch("@@ -5 +5 @@\n-old\n+new")) == [
        ("removed", 5, None),
        ("added", None, 5),
    ]


def test_no_newline_at_end_of_file_is_not_a_line() -> None:
    patch = (
        "@@ -1,1 +1,1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file"
    )
    assert kinds_and_numbers(parse_patch(patch)) == [("removed", 1, None), ("added", None, 1)]


def test_a_trailing_line_break_doesnt_add_a_line() -> None:
    assert len(parse_patch("@@ -1,1 +1,2 @@\n a\n+b\n")) == 2


def test_an_empty_context_line_is_still_a_line() -> None:
    lines = parse_patch("@@ -1,3 +1,3 @@\n a\n\n-c\n+d")
    assert kinds_and_numbers(lines) == [
        ("context", 1, 1),
        ("context", 2, 2),
        ("removed", 3, None),
        ("added", None, 3),
    ]
    assert lines[1].text == ""


def test_windows_line_endings_are_removed_from_the_text() -> None:
    lines = parse_patch("@@ -1,1 +1,1 @@\r\n-old\r\n+new\r")
    assert [line.text for line in lines] == ["old", "new"]
    assert lines[0].kind is LineKind.REMOVED


def test_text_that_looks_like_a_marker_inside_a_line_is_kept() -> None:
    lines = parse_patch("@@ -0,0 +1,2 @@\n+-1 is a number\n++= adds")
    assert [line.text for line in lines] == ["-1 is a number", "+= adds"]


def test_an_empty_patch_has_no_lines() -> None:
    assert parse_patch("") == ()
