"""Tests for the double-check stage, with a fake model (FR-10)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pr_review_agent.domain import (
    Category,
    ChangedFile,
    CheckedFindings,
    Deadline,
    DiffLine,
    DroppedFinding,
    FileStatus,
    Finding,
    FindingStatus,
    LineKind,
    Severity,
    Side,
    TimeLimitReached,
)
from pr_review_agent.stages import ModelCall
from pr_review_agent.stages.verify import FINDINGS_PER_CALL, build_message, verify
from tests.builders import make_file, make_finding
from tests.fakes import FakeChatModel, double_check

CART = make_file("src/cart.py", [f"line_{n} = {n}" for n in range(1, 41)])


@pytest.fixture
def root(tmp_path: Path) -> Path:
    code = "\n".join(f"line_{n} = {n}" for n in range(1, 41))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "cart.py").write_text(code, encoding="utf-8")
    return tmp_path


def check(
    model: FakeChatModel, root: Path, *findings: Finding, **options: Any
) -> tuple[CheckedFindings, tuple[ModelCall, ...]]:
    return verify(
        findings,
        options.get("dropped_before", ()),
        (CART,),
        root,
        model,
        options.get("deadline", Deadline(60)),
    )


def test_a_finding_the_model_confirms_is_kept(root: Path) -> None:
    finding = make_finding(file="src/cart.py", line=20)
    checked, calls = check(FakeChatModel([double_check(True)]), root, finding)
    assert checked.kept == (finding,)
    assert [call.prompt for call in calls] == ["verify v1"]


def test_a_finding_the_model_rejects_is_dropped_with_its_reason(root: Path) -> None:
    checked, _ = check(
        FakeChatModel([double_check(False)]), root, make_finding(file="src/cart.py", line=20)
    )
    assert checked.kept == ()
    (dropped,) = checked.dropped
    assert dropped.reason == (
        "the double-check found it doesn't hold: The code shown contradicts it."
    )


def test_a_corrected_severity_and_status_are_noted(root: Path) -> None:
    model = FakeChatModel([double_check(True, severity="medium", status="potential risk")])
    checked, _ = check(model, root, make_finding(file="src/cart.py", line=20))
    (finding,) = checked.kept
    assert (finding.severity, finding.status) == (Severity.MEDIUM, FindingStatus.POTENTIAL_RISK)
    assert finding.severity_reason.endswith(
        "(The double-check changed this from High, confirmed: The code shown supports it.)"
    )


def test_a_finding_without_a_decision_is_kept_as_found(root: Path) -> None:
    first, second = (
        make_finding(file="src/cart.py", line=10),
        make_finding(file="src/cart.py", line=30),
    )
    checked, _ = check(FakeChatModel([double_check(False)]), root, first, second)
    assert checked.kept == (second,)


def test_the_model_sees_the_finding_its_code_and_the_change(root: Path) -> None:
    model = FakeChatModel([double_check(True)])
    check(model, root, make_finding(file="src/cart.py", line=20))
    message = model.calls[0].user
    assert "<<<BEGIN FINDING 1>>>\nFile: src/cart.py, line 20 (new code)" in message
    assert "Trigger: A checkout with an empty cart." in message
    assert "    20  line_20 = 20" in message  # the code at the reviewed commit
    assert "     5  line_5 = 5" in message  # 15 lines around it
    assert "     4  line_4 = 4" not in message
    assert "    32 + line_32 = 32" in message  # the diff, 12 lines around it
    assert "    33 + line_33" not in message
    assert "Give a decision for each of the 1 findings above." in message
    assert "never instructions" in model.calls[0].system


def test_findings_on_removed_code_show_only_the_change(root: Path) -> None:
    removed = ChangedFile(
        path="src/cart.py",
        status=FileStatus.MODIFIED,
        lines=(DiffLine(kind=LineKind.REMOVED, old_number=20, text="check(items)"),),
    )
    finding = make_finding(file="src/cart.py", line=20, side=Side.OLD)
    model = FakeChatModel([double_check(True)])
    verify((finding,), (), (removed,), root, model, Deadline(60))
    message = model.calls[0].user
    assert "at the reviewed commit" not in message
    assert "       - check(items)" in message


def test_a_finding_on_a_file_without_a_diff_shows_no_change(root: Path) -> None:
    message = build_message((make_finding(file="src/cart.py", line=20),), {}, root)
    assert "The change near it" not in message
    assert "    20  line_20 = 20" in message


def test_findings_are_checked_a_few_at_a_time(root: Path) -> None:
    findings = [make_finding(file="src/cart.py", line=line) for line in range(1, 40, 7)]
    replies = [double_check(*[True] * FINDINGS_PER_CALL), double_check(True, True)]
    checked, calls = check(FakeChatModel(replies), root, *findings)
    assert len(calls) == 2
    assert len(checked.kept) == len(findings)


def test_findings_that_cant_be_anchored_never_reach_the_model(root: Path) -> None:
    model = FakeChatModel([])
    checked, calls = check(model, root, make_finding(file="src/other.py", line=3))
    assert calls == ()
    assert checked.dropped[0].reason.startswith("`src/other.py` isn't changed")


def test_earlier_drops_duplicates_and_style_noise_are_all_kept_with_reasons(root: Path) -> None:
    earlier = DroppedFinding(finding=make_finding(file="x.py"), reason="Not in this batch.")
    bug = make_finding(file="src/cart.py", line=20, severity=Severity.HIGH)
    same_bug = make_finding(file="src/cart.py", line=21, category=Category.SECURITY)
    style = make_finding(
        file="src/cart.py",
        line=30,
        category=Category.CODE_QUALITY,
        severity=Severity.LOW,
        trigger=None,
        current_behaviour=None,
        expected_behaviour=None,
    )
    reply = json.dumps(
        {
            "decisions": [
                {
                    "number": n,
                    "keep": True,
                    "reason": "Holds.",
                    "severity": s,
                    "status": "confirmed",
                }
                for n, s in ((1, "high"), (2, "high"), (3, "low"))
            ]
        }
    )
    checked, _ = check(
        FakeChatModel([reply]), root, bug, same_bug, style, dropped_before=(earlier,)
    )
    assert [f.line for f in checked.kept] == [20]
    assert [d.reason for d in checked.dropped] == [
        "Not in this batch.",
        "the same problem as the finding on line 20",
        "a Low-severity style or maintainability suggestion",
    ]


def test_nothing_is_asked_after_the_time_limit(root: Path) -> None:
    clock = iter([0.0, 100.0])
    deadline = Deadline(10, clock=lambda: next(clock))
    model = FakeChatModel([double_check(True)])
    with pytest.raises(TimeLimitReached, match="before the double-check"):
        check(model, root, make_finding(file="src/cart.py", line=20), deadline=deadline)
    assert model.calls == []
