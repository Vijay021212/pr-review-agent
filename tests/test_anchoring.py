"""Tests for the plain-code rules of the double-check: anchoring, duplicates, style noise."""

from __future__ import annotations

import pytest

from pr_review_agent.anchoring import anchor, anchor_all, drop_style_noise, remove_duplicates
from pr_review_agent.domain import (
    Category,
    ChangedFile,
    DiffLine,
    FileStatus,
    LineKind,
    Severity,
    Side,
)
from tests.builders import make_finding

# Lines 10-12 context, 13-14 added, 15 context; old line 13 removed.
FILE = ChangedFile(
    path="src/cart.py",
    status=FileStatus.MODIFIED,
    lines=(
        DiffLine(kind=LineKind.CONTEXT, old_number=10, new_number=10, text="def total(xs):"),
        DiffLine(kind=LineKind.CONTEXT, old_number=11, new_number=11, text="    s = 0"),
        DiffLine(kind=LineKind.CONTEXT, old_number=12, new_number=12, text="    for x in xs:"),
        DiffLine(kind=LineKind.REMOVED, old_number=13, text="        s += x"),
        DiffLine(kind=LineKind.ADDED, new_number=13, text="        s += x * 2"),
        DiffLine(kind=LineKind.ADDED, new_number=14, text="    return s / len(xs)"),
        DiffLine(kind=LineKind.CONTEXT, old_number=14, new_number=15, text="# end"),
    ),
)


def test_a_finding_on_a_changed_line_stays() -> None:
    finding = make_finding(file="src/cart.py", line=14)
    assert anchor(finding, FILE) is finding


def test_a_finding_near_a_changed_line_moves_onto_it_and_names_where_it_was() -> None:
    moved = anchor(make_finding(file="src/cart.py", line=11), FILE)
    assert not isinstance(moved, str)
    assert moved.line == 13
    assert moved.affected_places == ("src/cart.py:11",)


def test_the_closest_changed_line_wins_and_an_earlier_one_breaks_a_tie() -> None:
    later = anchor(make_finding(file="src/cart.py", line=17), FILE)
    assert not isinstance(later, str)
    assert later.line == 14
    tie = ChangedFile(
        path="a.py",
        status=FileStatus.MODIFIED,
        lines=(
            DiffLine(kind=LineKind.ADDED, new_number=2, text="x"),
            DiffLine(kind=LineKind.ADDED, new_number=6, text="y"),
        ),
    )
    between = anchor(make_finding(file="a.py", line=4), tie)
    assert not isinstance(between, str)
    assert between.line == 2


def test_an_affected_place_isnt_listed_twice() -> None:
    finding = make_finding(file="src/cart.py", line=11, affected_places=("src/cart.py:11",))
    moved = anchor(finding, FILE)
    assert not isinstance(moved, str)
    assert moved.affected_places == ("src/cart.py:11",)


def test_a_finding_on_removed_code_anchors_on_removed_lines() -> None:
    on_removed = make_finding(file="src/cart.py", line=13, side=Side.OLD)
    assert anchor(on_removed, FILE) is on_removed
    near_removed = anchor(make_finding(file="src/cart.py", line=10, side=Side.OLD), FILE)
    assert not isinstance(near_removed, str)
    assert (near_removed.line, near_removed.side) == (13, Side.OLD)


def test_a_finding_too_far_from_any_change_is_dropped() -> None:
    reason = anchor(make_finding(file="src/cart.py", line=40), FILE)
    assert reason == "line 40 of `src/cart.py` isn't within 5 lines of a changed line"


def test_a_finding_on_new_code_of_a_deleted_file_is_dropped() -> None:
    deleted = ChangedFile(
        path="old.py",
        status=FileStatus.REMOVED,
        lines=(DiffLine(kind=LineKind.REMOVED, old_number=1, text="x = 1"),),
    )
    assert isinstance(anchor(make_finding(file="old.py", line=1), deleted), str)


def test_a_finding_on_a_file_the_pull_request_doesnt_change_is_dropped() -> None:
    reason = anchor(make_finding(file="src/report.py", line=3), None)
    assert reason == (
        "`src/report.py` isn't changed by this pull request, so it can't be commented on"
    )


def test_anchor_all_sorts_findings_into_kept_and_dropped() -> None:
    findings = (
        make_finding(file="src/cart.py", line=14),
        make_finding(file="src/report.py", line=3),
        make_finding(file="src/cart.py", line=12),
    )
    result = anchor_all(findings, (FILE,))
    assert [f.line for f in result.kept] == [14, 13]
    assert [d.finding.file for d in result.dropped] == ["src/report.py"]


# --- Duplicates ------------------------------------------------------------------------


def test_the_same_problem_reported_twice_is_kept_once_at_the_higher_severity() -> None:
    by_pass_a = make_finding(file="src/cart.py", line=14, severity=Severity.HIGH)
    by_pass_b = make_finding(
        file="src/cart.py", line=13, category=Category.SECURITY, severity=Severity.CRITICAL
    )
    result = remove_duplicates((by_pass_a, by_pass_b))
    assert result.kept == (by_pass_b,)
    (dropped,) = result.dropped
    assert dropped.finding == by_pass_a
    assert dropped.reason == "the same problem as the finding on line 13"


@pytest.mark.parametrize(
    "other",
    [
        make_finding(file="src/other.py", line=14),  # another file
        make_finding(file="src/cart.py", line=20),  # too far apart
        make_finding(  # a different kind of finding
            file="src/cart.py",
            line=14,
            category=Category.CODE_QUALITY,
            trigger=None,
            current_behaviour=None,
            expected_behaviour=None,
        ),
        make_finding(file="src/cart.py", line=14, side=Side.OLD),  # the other side of the diff
    ],
)
def test_different_problems_are_all_kept(other: object) -> None:
    first = make_finding(file="src/cart.py", line=14)
    result = remove_duplicates((first, other))  # type: ignore[arg-type]
    assert len(result.kept) == 2
    assert result.dropped == ()


def test_kept_findings_come_back_in_file_and_line_order() -> None:
    findings = (
        make_finding(file="b.py", line=1, severity=Severity.CRITICAL),
        make_finding(file="a.py", line=9, severity=Severity.LOW),
        make_finding(file="a.py", line=2, severity=Severity.MEDIUM),
    )
    kept = remove_duplicates(findings).kept
    assert [(f.file, f.line) for f in kept] == [("a.py", 2), ("a.py", 9), ("b.py", 1)]


# --- Style noise -----------------------------------------------------------------------


def test_low_severity_maintainability_suggestions_are_dropped() -> None:
    style = make_finding(
        category=Category.CODE_QUALITY,
        severity=Severity.LOW,
        trigger=None,
        current_behaviour=None,
        expected_behaviour=None,
    )
    low_bug = make_finding(severity=Severity.LOW)
    medium_quality = style.model_copy(update={"severity": Severity.MEDIUM})
    result = drop_style_noise((style, low_bug, medium_quality))
    assert result.kept == (low_bug, medium_quality)
    assert result.dropped[0].reason == "a Low-severity style or maintainability suggestion"
