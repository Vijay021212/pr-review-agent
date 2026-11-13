"""The data passed between the review stages, and the interfaces to the outside world.

Every stage takes these immutable types and returns new ones (design section 2.1), which is
what lets each stage be tested on its own. The same types validate the model's answers, so
an answer in the wrong shape is caught instead of flowing through the review (FR-9).

The stages reach GitHub and the model only through the `GitHub` and `ChatModel` interfaces
below; `factory.py` plugs in the real implementations and tests plug in fakes (NFR-8).

This module depends only on the standard library and Pydantic, never on another module of
this package, so every other module can import it without creating import cycles.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Annotated, Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# --- Errors ------------------------------------------------------------------------------


class SetupError(Exception):
    """Bad input or a setup problem the user can fix (FR-16). The command line exits with 2.

    Every setup error says what's wrong and how to fix it, for example:
    "Ollama isn't running at http://localhost:11434." / "Start the Ollama app, then try again."
    """

    def __init__(self, problem: str, fix: str) -> None:
        super().__init__(f"{problem}\nHow to fix it: {fix}")
        self.problem = problem
        self.fix = fix


class ReviewIncomplete(Exception):
    """The review couldn't be completed (NFR-4). The command line exits with 1.

    Raised when a stage fails or the hard time limit is reached. Nothing more is posted,
    and no recommendation is given, so a broken review never looks like a clean one.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"The review is incomplete: {reason}")
        self.reason = reason


class TimeLimitReached(ReviewIncomplete):
    """The hard time limit (NFR-3) ran out before the review finished."""


# --- Small value types -------------------------------------------------------------------

# Text that must contain something other than spaces.
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# A full Git commit SHA, as GitHub returns it.
CommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
LineNumber = Annotated[int, Field(ge=1)]


class _Frozen(BaseModel):
    """Base for every domain type: immutable, and unknown fields are an error.

    `extra="forbid"` matters most for model output: a misspelled or invented field is
    reported instead of silently ignored. Collections are tuples, so nothing inside a value
    can be changed after it's created either.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class PullRequestState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class FileStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"
    RENAMED = "renamed"


class LineKind(StrEnum):
    """One line of a diff: added, removed, or unchanged context around the change."""

    ADDED = "added"
    REMOVED = "removed"
    CONTEXT = "context"


class Side(StrEnum):
    """Which version of the file a finding's line number refers to."""

    NEW = "new"  # the code after the change
    OLD = "old"  # a removed line, numbered as in the code before the change


class Category(StrEnum):
    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    DESIGN_FIT = "design fit"
    CODE_QUALITY = "code quality"

    @property
    def is_bug_type(self) -> bool:
        """Bug, security and performance are bug-type; the others are maintainability (5.3)."""
        return self in (Category.BUG, Category.SECURITY, Category.PERFORMANCE)


class ReviewLevel(IntEnum):
    """How deep a problem is (requirements 5.1)."""

    OBVIOUS = 1
    LOGIC = 2
    FLOW = 3
    ARCHITECTURE = 4
    ADVERSARIAL = 5


class Severity(StrEnum):
    """How much harm a problem causes (requirements 5.2), most severe first."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        """0 for Critical up to 3 for Low, for sorting the most severe first."""
        return list(Severity).index(self)


class FindingStatus(StrEnum):
    CONFIRMED = "confirmed"  # shown from the available code
    POTENTIAL_RISK = "potential risk"  # possible, but not confirmable from the available code


class SkipReason(StrEnum):
    """Why a changed file wasn't reviewed (FR-3, FR-4)."""

    LOCK_FILE = "lock file"
    BINARY = "binary"
    GENERATED = "generated"
    IGNORED_BY_RULES = "ignored by rules"
    TOO_LARGE = "too large"

    @property
    def is_gap(self) -> bool:
        """Only "too large" leaves part of the change unreviewed (FR-3, rule 2(a) in 5.3).

        Lock, binary, generated and ignored files are skipped on purpose and listed in the
        summary, but they aren't gaps in the review.
        """
        return self is SkipReason.TOO_LARGE


class GoalSource(StrEnum):
    DESCRIPTION = "description"  # from the title, description or linked issue
    CODE = "code"  # worked out from the code, because the description was empty (FR-2)


class ToolAction(StrEnum):
    """The read-only tools of the explore stage (design section 5)."""

    LIST_DIR = "list_dir"
    OPEN_FILE = "open_file"
    SEARCH = "search"
    DONE = "done"


class ExploreStop(StrEnum):
    """Why the explore stage stopped; the summary says which (FR-5)."""

    DONE = "done"
    STEP_LIMIT = "step limit"
    TIME_LIMIT = "time limit"
    REPEATING = "repeating"  # the model kept asking for steps it had already taken


class RecommendationValue(StrEnum):
    APPROVE = "Approve"
    REQUEST_CHANGES = "Request Changes"
    NEEDS_HUMAN_REVIEW = "Needs Human Review"


# --- Pull requests -----------------------------------------------------------------------

_LINK_PATTERN = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)"
    r"(?:[/?#].*)?$"
)


class PullRequestRef(_Frozen):
    """Which pull request to review: the parts of its link."""

    owner: NonEmptyStr
    repo: NonEmptyStr
    number: Annotated[int, Field(ge=1)]

    @classmethod
    def parse(cls, link: str) -> Self:
        """Read a link like https://github.com/owner/repo/pull/7; a trailing /files is fine."""
        match = _LINK_PATTERN.match(link.strip())
        if match is None or int(match["number"]) < 1:
            raise SetupError(
                f"'{link}' isn't a GitHub pull request link.",
                "Use a link like https://github.com/owner/repo/pull/7.",
            )
        return cls(owner=match["owner"], repo=match["repo"], number=int(match["number"]))

    @property
    def link(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"


class DiffLine(_Frozen):
    """One line of a diff, with its line number in the old and/or the new file.

    Added lines exist only in the new file, removed lines only in the old one, and context
    lines in both. The numbers are what lets a finding point to an exact line.
    """

    kind: LineKind
    old_number: LineNumber | None = None
    new_number: LineNumber | None = None
    text: str

    @model_validator(mode="after")
    def _numbers_match_kind(self) -> Self:
        has_old, has_new, needs = _LINE_NUMBERS_BY_KIND[self.kind]
        if (self.old_number is not None, self.new_number is not None) != (has_old, has_new):
            raise ValueError(f"a {self.kind} line needs {needs}")
        return self


# For each kind of diff line: (has an old number, has a new number, the rule in words).
_LINE_NUMBERS_BY_KIND = {
    LineKind.ADDED: (False, True, "only a new line number"),
    LineKind.REMOVED: (True, False, "only an old line number"),
    LineKind.CONTEXT: (True, True, "both an old and a new line number"),
}


class ChangedFile(_Frozen):
    """A file changed by the pull request, with its diff."""

    path: NonEmptyStr
    status: FileStatus
    previous_path: str | None = None  # the old path of a renamed file
    lines: tuple[DiffLine, ...] = ()
    # GitHub leaves the diff out for binary files and very large diffs (NFR-10). Such a
    # file is skipped with a reason, never silently treated as an empty change.
    diff_available: bool = True

    @model_validator(mode="after")
    def _renames_have_previous_path(self) -> Self:
        if (self.status is FileStatus.RENAMED) != (self.previous_path is not None):
            raise ValueError("previous_path is required for renamed files, and only for them")
        return self

    @property
    def changed_line_count(self) -> int:
        return sum(1 for line in self.lines if line.kind is not LineKind.CONTEXT)

    @property
    def added_lines(self) -> frozenset[int]:
        """New-file numbers of the added lines: where comments on new code can go."""
        return frozenset(
            line.new_number
            for line in self.lines
            if line.kind is LineKind.ADDED and line.new_number is not None
        )

    @property
    def removed_lines(self) -> frozenset[int]:
        """Old-file numbers of the removed lines: where comments on removed code can go."""
        return frozenset(
            line.old_number
            for line in self.lines
            if line.kind is LineKind.REMOVED and line.old_number is not None
        )


class LinkedIssue(_Frozen):
    """An issue the pull request description says it fixes ("Fixes #12")."""

    number: Annotated[int, Field(ge=1)]
    title: str
    body: str = ""


class PullRequest(_Frozen):
    """Everything read about the pull request, fixed at one commit (FR-1, FR-19, FR-20)."""

    owner: NonEmptyStr
    repo: NonEmptyStr
    number: Annotated[int, Field(ge=1)]
    state: PullRequestState
    draft: bool = False
    title: str
    description: str = ""
    linked_issues: tuple[LinkedIssue, ...] = ()
    head_sha: CommitSha  # the reviewed commit: all code is read here (FR-19)
    # Where the head commit lives: the fork for pull requests from a fork (FR-20).
    head_owner: NonEmptyStr
    head_repo: NonEmptyStr
    base_branch: NonEmptyStr
    files: tuple[ChangedFile, ...] = ()
    # The repository's languages, most used first, for ranking files (design section 6).
    languages: tuple[str, ...] = ()

    @property
    def ref(self) -> PullRequestRef:
        return PullRequestRef(owner=self.owner, repo=self.repo, number=self.number)

    @property
    def is_fork(self) -> bool:
        return (self.head_owner, self.head_repo) != (self.owner, self.repo)


# --- Stage results -----------------------------------------------------------------------


class SkippedFile(_Frozen):
    path: NonEmptyStr
    reason: SkipReason


class ReviewScope(_Frozen):
    """Which files are reviewed, in which batches, and which were skipped (FR-3, FR-4)."""

    # Each batch fits the model's budget; every finding pass runs once per batch.
    batches: tuple[tuple[ChangedFile, ...], ...] = ()
    skipped: tuple[SkippedFile, ...] = ()

    @property
    def files(self) -> tuple[ChangedFile, ...]:
        """Every reviewed file, in ranked order."""
        return tuple(file for batch in self.batches for file in batch)

    @property
    def has_gaps(self) -> bool:
        """True when some changed file couldn't be reviewed (rule 2(a) in 5.3)."""
        return any(skip.reason.is_gap for skip in self.skipped)


class Understanding(_Frozen):
    """What the pull request is for, written before looking for problems (FR-2)."""

    goal: NonEmptyStr
    summary: NonEmptyStr
    risky_parts: tuple[NonEmptyStr, ...] = ()
    goal_unclear: bool
    goal_source: GoalSource


class ToolCall(_Frozen):
    """One step of the explore stage: which tool, on what, why, and what came back."""

    action: ToolAction
    target: str = ""  # the path opened or listed, or the text searched for
    reason: str = ""
    result: str = ""  # a short description of the result, for the summary and the run log


class Excerpt(_Frozen):
    """Lines of an unchanged file the explore stage read, kept for the finding passes."""

    path: NonEmptyStr
    start_line: LineNumber
    text: str


class ExploredContext(_Frozen):
    """What the explore stage learned about the code around the change (FR-5)."""

    project_map: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    excerpts: tuple[Excerpt, ...] = ()
    notes: tuple[NonEmptyStr, ...] = ()  # what the model wrote down as it explored
    stopped_by: ExploreStop | None = None  # None when exploring didn't run


class Finding(_Frozen):
    """One problem, on one line (FR-9).

    This is also the shape the model must answer in, so these rules check its answers.
    """

    file: NonEmptyStr
    line: LineNumber
    side: Side = Side.NEW
    category: Category
    level: ReviewLevel
    severity: Severity
    severity_reason: NonEmptyStr
    status: FindingStatus
    explanation: NonEmptyStr
    # Required for bug-type findings: what triggers it, what happens now, what should happen.
    trigger: str | None = None
    current_behaviour: str | None = None
    expected_behaviour: str | None = None
    suggested_fix: str | None = None
    # Other places the problem affects, for example a caller in another file (FR-10).
    affected_places: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _bug_type_findings_explain_the_failure(self) -> Self:
        if self.category.is_bug_type:
            missing = [
                name
                for name in ("trigger", "current_behaviour", "expected_behaviour")
                if not (getattr(self, name) or "").strip()
            ]
            if missing:
                raise ValueError(
                    f"a {self.category} finding must say "
                    "what triggers it, what happens now and what should happen instead; "
                    f"missing: {', '.join(missing)}"
                )
        return self


class DroppedFinding(_Frozen):
    finding: Finding
    reason: NonEmptyStr


class CheckedFindings(_Frozen):
    """The findings after the double-check (FR-10, FR-11)."""

    kept: tuple[Finding, ...] = ()
    dropped: tuple[DroppedFinding, ...] = ()
    # Already posted by an earlier run, or on a thread a person marked as resolved.
    repeated: tuple[Finding, ...] = ()


class Recommendation(_Frozen):
    """The informational recommendation, decided by fixed rules in code (FR-12, 5.3)."""

    value: RecommendationValue
    rule: Annotated[int, Field(ge=1, le=3)]  # the rule in requirements 5.3 that decided it
    reasons: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]


class StageTiming(_Frozen):
    stage: NonEmptyStr
    seconds: Annotated[float, Field(ge=0)]


class ReviewResult(_Frozen):
    """Everything the review produced (FR-14). This is what `review_pull_request()` returns."""

    pull_request: PullRequest
    scope: ReviewScope
    understanding: Understanding
    explored: ExploredContext
    findings: CheckedFindings
    recommendation: Recommendation
    provider: NonEmptyStr
    model: NonEmptyStr
    prompt_versions: tuple[NonEmptyStr, ...] = ()  # for example "understand v1"
    timings: tuple[StageTiming, ...] = ()
    dry_run: bool
    review_url: str | None = None  # where the findings were posted, if they were

    @property
    def seconds(self) -> float:
        return sum(timing.seconds for timing in self.timings)


# --- The hard time limit -----------------------------------------------------------------


class Deadline:
    """The hard time limit of one review (NFR-3), started at preflight.

    Every slow step (model calls, GitHub calls, the explore loop) asks how much time is
    left, so a stuck model can never make a review run forever. The clock can be replaced
    in tests, so they never have to wait.
    """

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        if seconds <= 0:
            raise ValueError("a deadline needs a positive number of seconds")
        self._clock = clock
        self.seconds = seconds
        self._started = clock()

    def elapsed(self) -> float:
        return self._clock() - self._started

    def remaining(self) -> float:
        """Seconds left, never below zero."""
        return max(0.0, self.seconds - self.elapsed())

    @property
    def expired(self) -> bool:
        return self.remaining() <= 0

    def check(self, doing: str) -> None:
        """Raise `TimeLimitReached` if time is up; `doing` says what was about to happen."""
        if self.expired:
            # "the 20-minute time limit", or "the 3-second time limit" for short limits.
            limit = (
                f"{self.seconds / 60:g}-minute"
                if self.seconds >= 60
                else f"{self.seconds:g}-second"
            )
            raise TimeLimitReached(f"the {limit} time limit was reached before {doing}")


# --- Interfaces --------------------------------------------------------------------------


class ModelReply(_Frozen):
    """One answer from a model, before it's checked against the expected type."""

    text: str  # the raw JSON text the model returned
    model: NonEmptyStr
    input_tokens: Annotated[int, Field(ge=0)] = 0
    output_tokens: Annotated[int, Field(ge=0)] = 0
    seconds: Annotated[float, Field(ge=0)] = 0.0


class ChatModel(Protocol):
    """A model that answers with JSON matching a schema (design section 4, ADR-003).

    Implemented by one small wrapper per provider. Checking the answer against its type,
    and the one retry when it doesn't match, happen outside the wrappers, so they work the
    same for every provider.
    """

    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any], deadline: Deadline
    ) -> ModelReply: ...


class GitHub(Protocol):
    """Everything the review needs from GitHub (design section 3)."""

    def get_pull_request(self, ref: PullRequestRef, deadline: Deadline) -> PullRequest:
        """The pull request's details and changed files, fixed at its current head commit."""
        ...

    def download_snapshot(
        self, pull_request: PullRequest, folder: Path, deadline: Deadline
    ) -> Path:
        """Unpack the repository at the reviewed commit into `folder`; return its root."""
        ...
