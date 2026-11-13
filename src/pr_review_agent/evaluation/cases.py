"""Test pull requests stored as folders (design section 13).

    eval/cases/03-breaks-a-caller/
      case.yaml   title, description, kind, expected recommendation, held_out, problems
      base/       the repository before the change
      head/       the repository after the change

The diff is computed from `base/` and `head/`, so writing a case means writing ordinary
code. Each planted problem names a piece of text on its first line in `head/` rather than
a line number, so editing a case can't silently move the answer.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pr_review_agent.domain import (
    Category,
    ChangedFile,
    FileStatus,
    NonEmptyStr,
    PullRequest,
    PullRequestState,
    RecommendationValue,
    ReviewLevel,
    SetupError,
)
from pr_review_agent.github.diff import parse_patch

CASE_FILE = "case.yaml"


class CaseKind(StrEnum):
    PLANTED = "planted"  # one or more planted problems
    CLEAN = "clean"  # no problem: any comment is a false alarm
    INJECTION = "injection"  # tries to give the model instructions (NFR-5)


class PlantedProblem(BaseModel):
    """One problem put into a case on purpose, as written in case.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: NonEmptyStr
    marker: NonEmptyStr  # text on the problem's first line in head/
    lines: Annotated[int, Field(ge=1)] = 1  # how many lines the problem spans
    level: ReviewLevel
    category: Category
    what: NonEmptyStr  # a person's description, for the review sheet


class CaseSpec(BaseModel):
    """The contents of case.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: NonEmptyStr
    description: str = ""
    kind: CaseKind
    recommendation: RecommendationValue
    held_out: bool = False
    problems: tuple[PlantedProblem, ...] = ()


@dataclass(frozen=True)
class Located:
    """A planted problem with its lines found in head/."""

    problem: PlantedProblem
    first_line: int
    last_line: int


@dataclass(frozen=True)
class Case:
    name: str
    folder: Path
    spec: CaseSpec
    problems: tuple[Located, ...]
    pull_request: PullRequest

    @property
    def head(self) -> Path:
        return self.folder / "head"


def load_cases(folder: Path) -> tuple[Case, ...]:
    """Every case under `folder`, in name order."""
    if not folder.is_dir():
        raise SetupError(
            f"There's no folder of evaluation cases at {folder}.",
            "Give the folder that holds the case folders, for example eval/cases.",
        )
    cases = tuple(
        load_case(path) for path in sorted(folder.iterdir()) if (path / CASE_FILE).is_file()
    )
    if not cases:
        raise SetupError(
            f"{folder} holds no evaluation cases.",
            f"Each case is a folder with a {CASE_FILE}, a base/ and a head/ folder.",
        )
    return cases


def load_case(folder: Path) -> Case:
    try:
        raw = yaml.safe_load((folder / CASE_FILE).read_text(encoding="utf-8")) or {}
        spec = CaseSpec.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as error:
        raise SetupError(
            f"The evaluation case {folder.name} has an invalid {CASE_FILE}: {error}",
            f"Correct {folder / CASE_FILE}.",
        ) from None
    if spec.kind is CaseKind.CLEAN and spec.problems:
        raise SetupError(
            f"The clean case {folder.name} lists planted problems.",
            "Remove them, or change its kind to planted.",
        )
    if spec.kind is CaseKind.PLANTED and not spec.problems:
        raise SetupError(
            f"The case {folder.name} plants no problem.",
            "List its problems, or change its kind to clean.",
        )
    files = _changed_files(folder / "base", folder / "head")
    if not files:
        raise SetupError(
            f"The evaluation case {folder.name} changes nothing.",
            "Make head/ differ from base/.",
        )
    problems = tuple(_locate(folder, problem) for problem in spec.problems)
    return Case(
        name=folder.name,
        folder=folder,
        spec=spec,
        problems=problems,
        pull_request=_pull_request(folder.name, spec, files),
    )


def _locate(folder: Path, problem: PlantedProblem) -> Located:
    path = folder / "head" / problem.file
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for number, text in enumerate(lines, start=1):
        if problem.marker in text:
            return Located(problem, number, number + problem.lines - 1)
    raise SetupError(
        f"In the evaluation case {folder.name}, the text {problem.marker!r} isn't in "
        f"head/{problem.file}.",
        "Use a piece of text from the planted problem's first line.",
    )


def _changed_files(base: Path, head: Path) -> tuple[ChangedFile, ...]:
    """The diff between the two folders, as GitHub would list it."""
    paths = sorted(_text_files(base) | _text_files(head))
    files = []
    for path in paths:
        old = _read(base / path)
        new = _read(head / path)
        if old == new:
            continue
        diff = difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=3)
        patch = "\n".join(list(diff)[2:])  # without the --- and +++ header lines
        status = (
            FileStatus.ADDED
            if not (base / path).is_file()
            else FileStatus.REMOVED
            if not (head / path).is_file()
            else FileStatus.MODIFIED
        )
        files.append(ChangedFile(path=path, status=status, lines=parse_patch(patch)))
    return tuple(files)


def _text_files(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _pull_request(name: str, spec: CaseSpec, files: tuple[ChangedFile, ...]) -> PullRequest:
    # A stable, made-up commit id per case content, so reviews are tied to one version.
    digest = hashlib.sha1(name.encode(), usedforsecurity=False)
    for file in files:
        digest.update(file.path.encode())
        for line in file.lines:
            digest.update(line.text.encode())
    return PullRequest(
        owner="eval",
        repo=name,
        number=1,
        state=PullRequestState.OPEN,
        title=spec.title,
        description=spec.description,
        head_sha=digest.hexdigest(),
        head_owner="eval",
        head_repo=name,
        base_branch="main",
        files=files,
        languages=("Python",),
    )
