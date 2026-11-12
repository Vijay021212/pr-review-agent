"""Tests for the domain types: their validation rules, immutability and helpers."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pr_review_agent import domain
from pr_review_agent.domain import (
    Category,
    ChangedFile,
    ChatModel,
    CheckedFindings,
    Deadline,
    DiffLine,
    DroppedFinding,
    ExploredContext,
    FileStatus,
    Finding,
    GitHub,
    GoalSource,
    LineKind,
    ModelReply,
    PullRequest,
    PullRequestRef,
    Recommendation,
    RecommendationValue,
    ReviewIncomplete,
    ReviewLevel,
    ReviewResult,
    ReviewScope,
    SetupError,
    Severity,
    SkippedFile,
    SkipReason,
    StageTiming,
    TimeLimitReached,
    Understanding,
)
from tests.builders import SHA, make_file, make_finding, make_pull_request


class FakeClock:
    """A clock the test moves forward by hand."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make_understanding(**overrides: Any) -> Understanding:
    values: dict[str, Any] = {
        "goal": "Give members a discount.",
        "summary": "Adds a discount function.",
        "goal_unclear": False,
        "goal_source": GoalSource.DESCRIPTION,
        **overrides,
    }
    return Understanding(**values)


# --- Errors ------------------------------------------------------------------------------


def test_setup_error_says_what_is_wrong_and_how_to_fix_it() -> None:
    error = SetupError("Ollama isn't running.", "Start the Ollama app.")
    assert str(error) == "Ollama isn't running.\nHow to fix it: Start the Ollama app."
    assert (error.problem, error.fix) == ("Ollama isn't running.", "Start the Ollama app.")


def test_time_limit_reached_is_an_incomplete_review() -> None:
    error = TimeLimitReached("the limit was reached")
    assert isinstance(error, ReviewIncomplete)
    assert str(error) == "The review is incomplete: the limit was reached"


# --- Pull request links ------------------------------------------------------------------


@pytest.mark.parametrize(
    "link",
    [
        "https://github.com/octo/shop/pull/7",
        "https://github.com/octo/shop/pull/7/files",
        "https://github.com/octo/shop/pull/7#discussion_r1",
        "https://github.com/octo/shop/pull/7?w=1",
        "http://www.github.com/octo/shop/pull/7",
        "  https://github.com/octo/shop/pull/7  ",
    ],
)
def test_parse_reads_owner_repo_and_number(link: str) -> None:
    assert PullRequestRef.parse(link) == PullRequestRef(owner="octo", repo="shop", number=7)


@pytest.mark.parametrize(
    "link",
    [
        "",
        "octo/shop#7",
        "https://github.com/octo/shop/issues/7",
        "https://github.com/octo/shop/pull/0",
        "https://github.com/octo/shop/pull/7x",
        "https://gitlab.com/octo/shop/pull/7",
        "https://github.example.com/octo/shop/pull/7",
    ],
)
def test_parse_rejects_anything_but_a_pull_request_link(link: str) -> None:
    with pytest.raises(SetupError, match="isn't a GitHub pull request link"):
        PullRequestRef.parse(link)


def test_ref_link_and_text() -> None:
    ref = PullRequestRef(owner="octo", repo="shop", number=7)
    assert ref.link == "https://github.com/octo/shop/pull/7"
    assert str(ref) == "octo/shop#7"


# --- Diffs -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "old", "new"),
    [(LineKind.ADDED, None, 3), (LineKind.REMOVED, 3, None), (LineKind.CONTEXT, 3, 4)],
)
def test_diff_line_accepts_the_numbers_its_kind_needs(
    kind: LineKind, old: int | None, new: int | None
) -> None:
    line = DiffLine(kind=kind, old_number=old, new_number=new, text="x")
    assert (line.old_number, line.new_number) == (old, new)


@pytest.mark.parametrize(
    ("kind", "old", "new", "message"),
    [
        (LineKind.ADDED, 3, 3, "only a new line number"),
        (LineKind.ADDED, None, None, "only a new line number"),
        (LineKind.REMOVED, None, 3, "only an old line number"),
        (LineKind.CONTEXT, 3, None, "both an old and a new line number"),
    ],
)
def test_diff_line_rejects_numbers_that_dont_fit_its_kind(
    kind: LineKind, old: int | None, new: int | None, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        DiffLine(kind=kind, old_number=old, new_number=new, text="x")


def test_diff_line_numbers_start_at_one() -> None:
    with pytest.raises(ValidationError):
        DiffLine(kind=LineKind.ADDED, new_number=0, text="x")


def test_changed_file_knows_its_added_and_removed_lines() -> None:
    file = ChangedFile(
        path="app.py",
        status=FileStatus.MODIFIED,
        lines=(
            DiffLine(kind=LineKind.CONTEXT, old_number=1, new_number=1, text="a"),
            DiffLine(kind=LineKind.REMOVED, old_number=2, text="b"),
            DiffLine(kind=LineKind.ADDED, new_number=2, text="c"),
            DiffLine(kind=LineKind.ADDED, new_number=3, text="d"),
        ),
    )
    assert file.added_lines == {2, 3}
    assert file.removed_lines == {2}
    assert file.changed_line_count == 3
    assert file.diff_available


def test_renamed_file_needs_its_previous_path() -> None:
    renamed = make_file(status=FileStatus.RENAMED, previous_path="old.py")
    assert renamed.previous_path == "old.py"
    with pytest.raises(ValidationError, match="previous_path is required"):
        make_file(status=FileStatus.RENAMED)
    with pytest.raises(ValidationError, match="previous_path is required"):
        make_file(status=FileStatus.MODIFIED, previous_path="old.py")


def test_changed_file_path_cant_be_blank() -> None:
    with pytest.raises(ValidationError):
        make_file(path="   ")


# --- Pull requests -----------------------------------------------------------------------


def test_pull_request_ref_and_fork() -> None:
    pull_request = make_pull_request()
    assert pull_request.ref == PullRequestRef(owner="octo", repo="shop", number=7)
    assert not pull_request.is_fork
    assert make_pull_request(head_owner="someone").is_fork


@pytest.mark.parametrize("sha", ["abc123", SHA.upper(), SHA + "0", "g" * 40])
def test_pull_request_needs_a_full_commit_sha(sha: str) -> None:
    with pytest.raises(ValidationError, match="head_sha"):
        make_pull_request(head_sha=sha)


def test_pull_request_number_starts_at_one() -> None:
    with pytest.raises(ValidationError, match="number"):
        make_pull_request(number=0)


def test_types_are_immutable() -> None:
    pull_request = make_pull_request()
    with pytest.raises(ValidationError, match="frozen"):
        pull_request.title = "changed"  # type: ignore[misc]


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        make_pull_request(reviewer="someone")


def test_lists_are_stored_as_tuples() -> None:
    pull_request = make_pull_request(files=[make_file()])
    assert isinstance(pull_request.files, tuple)


# --- Findings ----------------------------------------------------------------------------


def test_finding_from_model_json() -> None:
    finding = Finding.model_validate_json(
        """{"file": "src/app.py", "line": "12", "category": "bug", "level": 2,
            "severity": "high", "severity_reason": "Checkout fails.",
            "status": "potential risk", "explanation": "Divides by zero.",
            "trigger": "Empty cart.", "current_behaviour": "Crash.",
            "expected_behaviour": "Average 0.", "affected_places": ["src/cart.py:40"]}"""
    )
    assert finding.line == 12
    assert finding.level is ReviewLevel.LOGIC
    assert finding.severity is Severity.HIGH
    assert finding.affected_places == ("src/cart.py:40",)


@pytest.mark.parametrize("category", [Category.BUG, Category.SECURITY, Category.PERFORMANCE])
@pytest.mark.parametrize("field", ["trigger", "current_behaviour", "expected_behaviour"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_bug_type_findings_must_explain_the_failure(
    category: Category, field: str, value: str | None
) -> None:
    with pytest.raises(ValidationError, match=f"missing: {field}"):
        make_finding(category=category, **{field: value})


@pytest.mark.parametrize("category", [Category.DESIGN_FIT, Category.CODE_QUALITY])
def test_maintainability_findings_dont_need_the_failure_fields(category: Category) -> None:
    finding = make_finding(
        category=category, trigger=None, current_behaviour=None, expected_behaviour=None
    )
    assert finding.category is category


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("level", 0),
        ("level", 6),
        ("line", 0),
        ("severity", "urgent"),
        ("category", "style"),
        ("status", "maybe"),
        ("explanation", " "),
        ("severity_reason", ""),
        ("file", ""),
    ],
)
def test_finding_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match=field):
        make_finding(**{field: value})


def test_category_bug_type() -> None:
    assert [c for c in Category if c.is_bug_type] == [
        Category.BUG,
        Category.SECURITY,
        Category.PERFORMANCE,
    ]


def test_severity_rank_puts_the_most_severe_first() -> None:
    assert sorted(Severity, key=lambda severity: -severity.rank)[-1] is Severity.CRITICAL
    assert [s.rank for s in Severity] == [0, 1, 2, 3]


# --- Scope, recommendation and result ----------------------------------------------------


def test_only_too_large_files_are_gaps() -> None:
    assert [reason for reason in SkipReason if reason.is_gap] == [SkipReason.TOO_LARGE]


def test_review_scope_lists_files_in_batch_order_and_gaps() -> None:
    first, second, third = make_file("a.py"), make_file("b.py"), make_file("c.py")
    scope = ReviewScope(
        batches=((first, second), (third,)),
        skipped=(SkippedFile(path="uv.lock", reason=SkipReason.LOCK_FILE),),
    )
    assert [file.path for file in scope.files] == ["a.py", "b.py", "c.py"]
    assert not scope.has_gaps
    with_gap = scope.model_copy(
        update={"skipped": (SkippedFile(path="big.py", reason=SkipReason.TOO_LARGE),)}
    )
    assert with_gap.has_gaps


def test_understanding_needs_a_goal_and_summary() -> None:
    assert make_understanding().goal == "Give members a discount."
    with pytest.raises(ValidationError, match="goal"):
        make_understanding(goal="")
    with pytest.raises(ValidationError, match="summary"):
        make_understanding(summary="  ")


def test_recommendation_needs_a_reason_and_a_rule_from_5_3() -> None:
    recommendation = Recommendation(
        value=RecommendationValue.APPROVE, rule=3, reasons=("Nothing found.",)
    )
    assert recommendation.value == "Approve"
    with pytest.raises(ValidationError, match="reasons"):
        Recommendation(value=RecommendationValue.APPROVE, rule=3, reasons=())
    with pytest.raises(ValidationError, match="rule"):
        Recommendation(value=RecommendationValue.APPROVE, rule=4, reasons=("x",))


def test_review_result_adds_up_its_timings() -> None:
    result = ReviewResult(
        pull_request=make_pull_request(),
        scope=ReviewScope(),
        understanding=make_understanding(),
        explored=ExploredContext(),
        findings=CheckedFindings(
            kept=(make_finding(),),
            dropped=(DroppedFinding(finding=make_finding(line=2), reason="Not a real problem."),),
        ),
        recommendation=Recommendation(
            value=RecommendationValue.REQUEST_CHANGES, rule=1, reasons=("1 High finding",)
        ),
        provider="ollama",
        model="qwen2.5-coder:7b",
        timings=(StageTiming(stage="fetch", seconds=1.5), StageTiming(stage="find", seconds=2)),
        dry_run=True,
    )
    assert result.seconds == 3.5
    assert result.explored.stopped_by is None
    with pytest.raises(ValidationError):
        StageTiming(stage="fetch", seconds=-1)


def test_model_reply_token_counts_cant_be_negative() -> None:
    assert ModelReply(text="{}", model="m").output_tokens == 0
    with pytest.raises(ValidationError):
        ModelReply(text="{}", model="m", input_tokens=-1)


# --- Deadline ----------------------------------------------------------------------------


def test_deadline_counts_down_with_its_clock() -> None:
    clock = FakeClock()
    deadline = Deadline(60, clock=clock)
    clock.now += 45
    assert deadline.elapsed() == 45
    assert deadline.remaining() == 15
    assert not deadline.expired
    deadline.check("the find stage")  # time left: nothing happens


def test_deadline_raises_once_time_is_up() -> None:
    clock = FakeClock()
    deadline = Deadline(1200, clock=clock)
    clock.now += 1300
    assert deadline.remaining() == 0
    assert deadline.expired
    with pytest.raises(TimeLimitReached, match="20-minute time limit was reached before posting"):
        deadline.check("posting")


@pytest.mark.parametrize("seconds", [0, -5])
def test_deadline_needs_a_positive_limit(seconds: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        Deadline(seconds)


# --- Interfaces --------------------------------------------------------------------------


class ScriptedModel:
    """The smallest class that satisfies ChatModel; mypy checks the match below."""

    provider = "fake"
    model = "scripted"

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any], deadline: Deadline
    ) -> ModelReply:
        return ModelReply(text='{"ok": true}', model=self.model)


class OnePullRequest:
    """The smallest class that satisfies GitHub."""

    def get_pull_request(self, ref: PullRequestRef, deadline: Deadline) -> PullRequest:
        return make_pull_request(owner=ref.owner, repo=ref.repo, number=ref.number)


def test_any_class_with_the_right_methods_fits_the_interfaces() -> None:
    model: ChatModel = ScriptedModel()
    github: GitHub = OnePullRequest()
    deadline = Deadline(10)
    assert model.complete_json("system", "user", {}, deadline).text == '{"ok": true}'
    ref = PullRequestRef(owner="a", repo="b", number=3)
    assert github.get_pull_request(ref, deadline).number == 3


def test_domain_depends_only_on_the_standard_library_and_pydantic() -> None:
    """Keeps the design rule true: domain.py never imports the rest of the package,
    a GitHub client or a model provider."""
    tree = ast.parse(Path(domain.__file__).read_text(encoding="utf-8"))
    imported = {
        name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or "."]
        )
    }
    allowed = set(sys.stdlib_module_names) | {"__future__", "pydantic"}
    assert imported <= allowed, f"domain.py imports {sorted(imported - allowed)}"
