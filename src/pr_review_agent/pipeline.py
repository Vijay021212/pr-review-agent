"""Runs the review stages in a fixed order (design section 2, ADR-002).

    fetch -> select files -> understand -> find problems -> recommend

**Failure rule (NFR-4):** if any stage fails, or the hard time limit is reached, the
pipeline stops at once and raises `ReviewIncomplete`: nothing more runs, no recommendation
is given, and a broken review can never look like a clean one. A setup problem (a bad
token, a closed pull request) is raised as `SetupError` instead, so it can say how to fix it.

Progress goes to the `pr_review_agent` logger; the command line shows it, and code using
the package can show or ignore it like any other Python logging.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from pr_review_agent.config import Settings
from pr_review_agent.domain import (
    CheckedFindings,
    Deadline,
    ExploredContext,
    PullRequestRef,
    ReviewIncomplete,
    ReviewResult,
    SetupError,
    StageTiming,
)
from pr_review_agent.factory import Components
from pr_review_agent.recommendation import recommend
from pr_review_agent.selection import select_files
from pr_review_agent.sensitive import find_sensitive
from pr_review_agent.stages import ModelCall
from pr_review_agent.stages.fetch import fetch
from pr_review_agent.stages.find import PASS_A, find_problems
from pr_review_agent.stages.understand import understand

log = logging.getLogger("pr_review_agent")


class _Stages:
    """Times each stage and turns any unexpected error into an incomplete review."""

    def __init__(self) -> None:
        self.timings: list[StageTiming] = []

    @contextmanager
    def run(self, name: str, doing: str) -> Iterator[None]:
        log.info("%s...", doing)
        started = time.perf_counter()
        try:
            yield
        except (SetupError, ReviewIncomplete):
            raise
        except Exception as error:
            raise ReviewIncomplete(
                f"the {name} stage failed ({type(error).__name__}: {error})"
            ) from error
        finally:
            self.timings.append(StageTiming(stage=name, seconds=time.perf_counter() - started))


def run_review(
    ref: PullRequestRef,
    components: Components,
    settings: Settings,
    deadline: Deadline,
    *,
    dry_run: bool,
) -> ReviewResult:
    stages = _Stages()
    calls: list[ModelCall] = []
    model = components.model

    with stages.run("fetch", f"Reading pull request {ref}"):
        pull_request = fetch(components.github, ref, deadline)
    with stages.run("select files", "Choosing the files to review"):
        scope = select_files(pull_request, context_tokens=settings.context_tokens)
    with stages.run("understand", "Working out what the pull request is for"):
        understanding, call = understand(pull_request, scope, model, deadline)
        calls.append(call)
    explored = ExploredContext()
    with stages.run("find", f"Looking for problems in {len(scope.batches)} batch(es) of files"):
        found = find_problems(PASS_A, pull_request, scope, understanding, explored, model, deadline)
        calls += found.calls
    findings = CheckedFindings(kept=found.findings, dropped=found.dropped)
    with stages.run("recommend", "Applying the recommendation rules"):
        changed_paths = [file.path for file in pull_request.files]
        recommendation = recommend(findings, scope, understanding, find_sensitive(changed_paths))

    result = ReviewResult(
        pull_request=pull_request,
        scope=scope,
        understanding=understanding,
        explored=explored,
        findings=findings,
        recommendation=recommendation,
        provider=model.provider,
        model=model.model,
        prompt_versions=tuple(dict.fromkeys(call.prompt for call in calls)),
        timings=tuple(stages.timings),
        dry_run=dry_run,
    )
    # The result leaves the program (to the terminal, or to code using the package), so
    # every text in it is masked, including anything the model quoted (FR-21).
    return ReviewResult.model_validate(components.masker.mask_data(result.model_dump(mode="json")))
